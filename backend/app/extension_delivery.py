"""Connected Chrome devices poll for private, recipient-scoped alerts."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select, text

from .auth import Actor, Db
from .models import ExtensionInstallation, RecipientPreferences, Reminder, User, now

router = APIRouter(prefix="/api/extension")


def extension_ready(db, user_id):
    return bool(
        db.scalar(
            select(ExtensionInstallation.id)
            .where(
                ExtensionInstallation.user_id == user_id,
                ExtensionInstallation.notifications_enabled.is_(True),
                ExtensionInstallation.last_seen_at > now() - timedelta(days=7),
            )
            .limit(1)
        )
    )


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    installation_id: UUID
    notifications_enabled: bool


@router.post("/installation")
def heartbeat(body: Installation, actor: Actor, db: Db):
    if actor.is_demo:
        raise HTTPException(403, "Connect Gmail to register this extension")
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))
    db.scalar(select(User).where(User.id == actor.id).with_for_update(key_share=True))
    row = db.get(ExtensionInstallation, str(body.installation_id))
    if row and row.user_id != actor.id:
        raise HTTPException(409, "Sign out of the other account in this Chrome profile first")
    if not row:
        # Do not retain unlimited abandoned installs. Cleanup is scoped to this user.
        db.execute(
            delete(ExtensionInstallation).where(
                ExtensionInstallation.user_id == actor.id,
                ExtensionInstallation.last_seen_at < now() - timedelta(days=30),
            )
        )
        count = db.scalar(
            select(func.count())
            .select_from(ExtensionInstallation)
            .where(ExtensionInstallation.user_id == actor.id)
        )
        if count >= 10:
            raise HTTPException(409, "This account already has ten connected Chrome profiles")
        row = ExtensionInstallation(id=str(body.installation_id), user_id=actor.id)
        db.add(row)
    row.notifications_enabled, row.last_seen_at = body.notifications_enabled, now()
    db.commit()
    return {"registered": True}


@router.post("/reminders/{reminder_id}/notified")
def notified(reminder_id: str, actor: Actor, db: Db):
    value = db.get(Reminder, reminder_id)
    if not value or value.recipient_user_id != actor.id:
        raise HTTPException(404, "Reminder not found")
    prefs = db.get(RecipientPreferences, actor.id)
    sender = db.get(User, value.sender_user_id)
    if (
        value.status != "delivered"
        or not prefs
        or not prefs.reminders_enabled
        or not prefs.notification_enabled
        or sender.email in prefs.blocked_senders
    ):
        raise HTTPException(409, "Recipient notification preferences changed")
    value.notification_status = "extension_notified"
    db.commit()
    return {"accepted_by_chrome": True}
