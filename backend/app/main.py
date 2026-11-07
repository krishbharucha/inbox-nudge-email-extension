import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select, text

from .auth import Actor, Db, issue_token
from .config import get_settings
from .db import SessionLocal
from .dispatch import delivery_loop, dispatch_one
from .email_delivery import router as email_router
from .extension_delivery import router as extension_router
from .extension_sessions import router as extension_auth_router
from .gmail import account_for, addresses
from .gmail import headers as gmail_headers
from .gmail import router as gmail_router
from .google_http import GMAIL_SEND_SCOPE, GmailClient, GoogleUnavailable
from .google_oauth import router as oauth_router
from .inbox import router as inbox_router
from .migrate import main as migrate
from .models import (
    AuditLog,
    ConnectedAccount,
    EmailThread,
    RecipientPreferences,
    Reminder,
    User,
    now,
)
from .push import router as push_router
from .reminders import create_reminder, eligibility, eligibility_view, preferences, reminder_view
from .request_security import RequestSecurityMiddleware
from .schemas import DemoLogin, PreferencesUpdate, ReminderCreate
from .seed import seed_demo

settings = get_settings()


@asynccontextmanager
async def lifespan(app):
    migrate()
    if settings.demo_mode:
        with SessionLocal() as db:
            seed_demo(db)
    stop = asyncio.Event()
    worker = asyncio.create_task(delivery_loop(stop)) if settings.delivery_worker_enabled else None
    yield
    stop.set()
    if worker:
        await worker


app = FastAPI(title="Inbox Nudge API", version="0.1.0", lifespan=lifespan)
app.include_router(oauth_router)
app.include_router(gmail_router)
app.include_router(inbox_router)
app.include_router(email_router)
app.include_router(extension_router)
app.include_router(extension_auth_router)
app.include_router(push_router)
origins = [s.strip() for s in settings.cors_origins.split(",") if s.strip()]
if settings.extension_id:
    origins.append(f"chrome-extension://{settings.extension_id}")
app.add_middleware(RequestSecurityMiddleware, settings=settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def invalid_input(request, exception):
    # Pydantic otherwise echoes rejected OAuth codes, refresh tokens, and bodies.
    return JSONResponse({"detail": "Invalid request parameters"}, status_code=422)


@app.middleware("http")
async def private_api_responses(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/api/health")
def health(db: Db):
    db.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "demo_mode": settings.demo_mode,
        "gmail_native_delivery": False,
        "google_configured": settings.google_configured,
        "extension_configured": bool(settings.extension_id),
        "extension_id": settings.extension_id,
        "deployment_environment": settings.app_env,
        "request_limits_enabled": settings.request_limits_enabled,
    }


@app.post("/api/auth/demo")
def demo_login(body: DemoLogin, db: Db):
    if not settings.demo_mode:
        raise HTTPException(404, "Not found")
    user = db.get(User, body.persona)
    if not user or not user.is_demo:
        raise HTTPException(404, "Demo account not available")
    account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == user.id))
    account.last_seen_at = now()
    db.commit()
    return {"access_token": issue_token(user), "token_type": "bearer", "expires_in": 3600}


@app.get("/api/me")
def me(actor: Actor, db: Db):
    account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == actor.id))
    used = db.scalar(
        select(func.count())
        .select_from(Reminder)
        .where(
            Reminder.sender_user_id == actor.id,
            Reminder.created_at >= now() - timedelta(days=1),
        )
    )
    return {
        "id": actor.id,
        "email": actor.email,
        "display_name": actor.display_name,
        "demo": actor.is_demo,
        "connection_status": account.connection_status if account else "disconnected",
        "daily_used": used,
        "daily_limit": settings.daily_limit,
        "cooldown_hours": settings.cooldown_hours,
        "provider": account.provider if account else None,
        "gmail_send_enabled": bool(account and GMAIL_SEND_SCOPE in account.granted_scopes),
    }


@app.get("/api/threads")
def threads(actor: Actor, db: Db):
    values = db.scalars(
        select(EmailThread)
        .where(EmailThread.sender_user_id == actor.id)
        .order_by(EmailThread.last_message_at.desc())
    ).all()
    return [
        {
            "id": t.id,
            "subject": t.subject,
            "preview": t.preview,
            "recipient_email": t.recipient_email,
            "last_message_at": t.last_message_at,
            "has_reply": t.has_reply,
            "eligibility": eligibility(db, t, actor) if actor.is_demo else "unable_to_verify",
            "provider_thread_id": t.provider_thread_id,
        }
        for t in values
    ]


@app.get("/api/threads/{thread_id}/eligibility")
def thread_eligibility(thread_id: str, actor: Actor, db: Db):
    thread = db.get(EmailThread, thread_id)
    if not thread or thread.sender_user_id != actor.id:
        raise HTTPException(404, "Conversation not found")
    result = eligibility_view(db, thread, actor)
    db.commit()
    return result


@app.post("/api/reminders", status_code=201)
def create(body: ReminderCreate, actor: Actor, db: Db, background_tasks: BackgroundTasks):
    value = create_reminder(db, actor, body.thread_id, body.idempotency_key, body.delivery_channel)
    if settings.delivery_worker_enabled:
        background_tasks.add_task(dispatch_one, value.id)
    return reminder_view(db, value, actor)


@app.get("/api/reminders")
def history(actor: Actor, db: Db, offset: int = 0, limit: int = 50):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(422, "Use offset >= 0 and limit between 1 and 100")
    values = db.scalars(
        select(Reminder)
        .where(or_(Reminder.sender_user_id == actor.id, Reminder.recipient_user_id == actor.id))
        .order_by(Reminder.created_at.desc(), Reminder.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return [reminder_view(db, r, actor) for r in values]


def accessible(db, id_, actor):
    value = db.get(Reminder, id_)
    if not value or actor.id not in (value.sender_user_id, value.recipient_user_id):
        raise HTTPException(404, "Reminder not found")
    return value


@app.get("/api/reminders/{reminder_id}")
def detail(reminder_id: str, actor: Actor, db: Db):
    return reminder_view(db, accessible(db, reminder_id, actor), actor)


@app.post("/api/reminders/{reminder_id}/reconcile")
def reconcile(reminder_id: str, actor: Actor, db: Db):
    value = accessible(db, reminder_id, actor)
    if value.sender_user_id != actor.id:
        raise HTTPException(403, "Only the sender can check an email send")
    if value.status != "email_unknown" or not value.outbound_message_id:
        return reminder_view(db, value, actor)
    try:
        client = GmailClient(db, account_for(db, actor.id))
        found = client.get(
            "/messages",
            {"q": f"rfc822msgid:{value.outbound_message_id}", "labelIds": "SENT", "maxResults": 2},
        )
        messages = found.get("messages", [])
        if len(messages) == 1:
            provider_id = messages[0].get("id", "")
            import re

            if not re.fullmatch(r"[a-fA-F0-9]{10,32}", provider_id):
                raise GoogleUnavailable("Invalid sent message reference")
            message = client.get(
                f"/messages/{provider_id}",
                {"format": "metadata", "metadataHeaders": ["Message-ID", "From", "To"]},
            )
            meta = gmail_headers(message)
            thread = db.get(EmailThread, value.thread_id)
            if (
                meta.get("message-id", "").strip() == value.outbound_message_id
                and addresses(meta.get("from", "")) == {actor.email}
                and addresses(meta.get("to", "")) == {thread.recipient_email}
                and "SENT" in message.get("labelIds", [])
            ):
                value.status, value.provider_message_id, value.delivered_at = (
                    "email_sent",
                    provider_id,
                    now(),
                )
        db.commit()
    except (GoogleUnavailable, ValueError, KeyError, TypeError):
        raise HTTPException(
            503, "Unable to check Gmail Sent. The reminder has not been resent."
        ) from None
    return reminder_view(db, value, actor)


@app.post("/api/reminders/{reminder_id}/dismiss")
def dismiss(reminder_id: str, actor: Actor, db: Db):
    value = accessible(db, reminder_id, actor)
    if value.recipient_user_id != actor.id:
        raise HTTPException(403, "Only the recipient can dismiss a reminder")
    if value.status == "dismissed":
        return reminder_view(db, value, actor)
    if value.status != "delivered":
        raise HTTPException(409, "Only a delivered reminder can be dismissed")
    value.status, value.dismissed_at = "dismissed", now()
    db.add(AuditLog(user_id=actor.id, action="reminder.dismissed", resource_id=value.id))
    db.commit()
    return reminder_view(db, value, actor)


@app.get("/api/inbox/priority")
def priority(actor: Actor, db: Db):
    prefs = preferences(db, actor.id)
    if not prefs.reminders_enabled:
        return []
    values = db.scalars(
        select(Reminder)
        .where(Reminder.recipient_user_id == actor.id, Reminder.status == "delivered")
        .order_by(Reminder.delivered_at.desc())
    ).all()
    seen, result = set(), []
    for value in values:
        view = reminder_view(db, value, actor)
        if value.thread_id not in seen and view["sender_email"] not in prefs.blocked_senders:
            result.append(view)
            seen.add(value.thread_id)
    return result


@app.get("/api/preferences", response_model=PreferencesUpdate)
def get_preferences(actor: Actor, db: Db):
    return preferences(db, actor.id)


@app.put("/api/preferences", response_model=PreferencesUpdate)
def update_preferences(body: PreferencesUpdate, actor: Actor, db: Db):
    value = db.scalar(
        select(RecipientPreferences)
        .where(
            RecipientPreferences.user_id == actor.id,
        )
        .with_for_update()
    ) or preferences(db, actor.id)
    value.reminders_enabled = body.reminders_enabled
    value.notification_enabled = body.notification_enabled
    value.blocked_senders = sorted(set(str(email).lower() for email in body.blocked_senders))
    db.add(AuditLog(user_id=actor.id, action="preferences.updated", resource_id=actor.id))
    db.commit()
    return value
