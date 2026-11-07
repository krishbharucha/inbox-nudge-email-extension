"""Small sender-confirmed replies. No automatic retry after an ambiguous send."""

import base64
import hashlib
import secrets
from email.headerregistry import Address
from email.message import EmailMessage
from html import escape
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from .auth import Db
from .config import get_settings
from .google_http import GMAIL_BASE, GoogleUnavailable, decrypt, encrypt, google_request
from .models import EmailPreference, User, now

router = APIRouter(prefix="/api/email/preferences")


def reminder_text(sender):
    name = " ".join(sender.display_name.split())
    return f"Hi,\n\nJust a gentle reminder about my earlier email in this conversation. When you have a chance, I'd appreciate your reply.\n\nThanks,\n{name}\n\nSent with Inbox Nudge."


def email_preference(db, sender, recipient_email):
    value = db.scalar(
        select(EmailPreference).where(
            EmailPreference.sender_user_id == sender.id,
            EmailPreference.recipient_email == recipient_email,
        )
    )
    if not value:
        token = secrets.token_urlsafe(32)
        value = EmailPreference(
            sender_user_id=sender.id,
            recipient_email=recipient_email,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            token_encrypted=encrypt(token),
        )
        db.add(value)
        db.flush()
    return value


def encode_email(db, reminder, sender, thread):
    prefs = email_preference(db, sender, thread.recipient_email)
    token = decrypt(prefs.token_encrypted)
    backend = urlsplit(get_settings().google_redirect_uri)
    unsubscribe = f"{backend.scheme}://{backend.netloc}/api/email/preferences/{quote(token, safe='')}/unsubscribe"
    message = EmailMessage()
    message["From"] = Address(
        display_name=" ".join(sender.display_name.split()), addr_spec=sender.email
    )
    message["To"] = thread.recipient_email
    message["Subject"] = " ".join(thread.subject.split())
    message["In-Reply-To"] = thread.original_message_id
    message["References"] = thread.original_message_id
    message["Message-ID"] = reminder.outbound_message_id
    message["Auto-Submitted"] = "auto-generated"
    message["List-Unsubscribe"] = f"<{unsubscribe}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(
        reminder_text(sender) + f"\n\nStop email reminders from this sender: {unsubscribe}\n"
    )
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def send_email(access_token, thread_id, raw):
    try:
        response = google_request(
            "POST",
            f"{GMAIL_BASE}/messages/send",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"threadId": thread_id, "raw": raw},
        )
    except (GoogleUnavailable, httpx.HTTPError):
        return "email_unknown", None
    if response.status_code == 200:
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("id"), str) and body["id"]:
                return "email_sent", body["id"]
        except (ValueError, TypeError):
            pass
        return "email_unknown", None
    # 5xx/timeouts can follow an accepted send. Never automatically resend.
    if response.status_code >= 500 or response.status_code in {408, 409}:
        return "email_unknown", None
    return "email_failed", None


def preference_by_token(db, token):
    if len(token) > 100:
        raise HTTPException(404, "Email preference link not found")
    value = db.scalar(
        select(EmailPreference).where(
            EmailPreference.token_hash == hashlib.sha256(token.encode()).hexdigest()
        )
    )
    if not value:
        raise HTTPException(404, "Email preference link not found")
    return value


@router.get("/{token}")
def get_email_preference(token: str, db: Db, response: Response):
    response.headers["Cache-Control"] = "no-store"
    value = preference_by_token(db, token)
    sender = db.get(User, value.sender_user_id)
    return {"sender_name": sender.display_name, "blocked": value.blocked_at is not None}


@router.get("/{token}/unsubscribe")
def unsubscribe_page(token: str, db: Db):
    # GET is deliberately side-effect free: mail scanners follow links.
    value = preference_by_token(db, token)
    sender = db.get(User, value.sender_user_id)
    content = (
        "<p>Email reminders from this sender are already stopped.</p>"
        if value.blocked_at
        else '<p>No account or sign-in is required.</p><form method="post"><button type="submit">Stop email reminders</button></form>'
    )
    return preference_html(
        f"<h1>Email reminder preferences</h1><p>Reminders from {escape(sender.display_name)}</p>{content}"
    )


def preference_html(content):
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Inbox Nudge · Email preferences</title><style>body{font:16px/1.7 system-ui;background:#f6f8f1;color:#254435;padding:32px}main{max-width:440px;margin:60px auto;background:white;padding:32px;border-radius:14px}h1{font:30px Georgia,serif}button{padding:12px 20px;border:0;border-radius:6px;background:#254435;color:white;cursor:pointer}</style><main>'
        + content
        + "</main></html>",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{token}/unsubscribe")
def unsubscribe(token: str, db: Db, response: Response, request: Request):
    value = preference_by_token(db, token)
    if not value.blocked_at:
        value.blocked_at = now()
        db.commit()
    response.headers["Cache-Control"] = "no-store"
    if "text/html" in request.headers.get("accept", ""):
        return preference_html(
            "<h1>Reminders stopped.</h1><p>Email reminders from this sender are stopped. You can close this tab.</p>"
        )
    return {"blocked": True}
