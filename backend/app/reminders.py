from datetime import UTC, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .delivery import delivery_status, verified_for_sender
from .email_delivery import reminder_text
from .extension_delivery import extension_ready
from .google_http import GMAIL_SEND_SCOPE
from .models import (
    AuditLog,
    ConnectedAccount,
    EmailPreference,
    EmailThread,
    PushSubscription,
    RecipientPreferences,
    Reminder,
    User,
    now,
)


def preferences(db: Session, user_id: str):
    value = db.get(RecipientPreferences, user_id)
    if not value:
        value = RecipientPreferences(user_id=user_id)
        db.add(value)
        db.flush()
    return value


def email_fallback_status(db, thread, sender):
    if sender.is_demo:
        return "recipient_not_connected"
    blocked = db.scalar(
        select(EmailPreference).where(
            EmailPreference.sender_user_id == sender.id,
            EmailPreference.recipient_email == thread.recipient_email,
            EmailPreference.blocked_at.is_not(None),
        )
    )
    if blocked:
        return "recipient_disabled"
    account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == sender.id))
    return (
        "email_ready"
        if account and GMAIL_SEND_SCOPE in account.granted_scopes
        else "email_permission_required"
    )


def eligibility(db: Session, thread: EmailThread, sender: User, ignore_reminder_id=None):
    settings = get_settings()
    daily = db.scalar(
        select(func.count())
        .select_from(Reminder)
        .where(
            Reminder.sender_user_id == sender.id,
            Reminder.created_at >= now() - timedelta(days=1),
            Reminder.id != ignore_reminder_id if ignore_reminder_id else True,
        )
    )
    if daily >= settings.daily_limit:
        return "rate_limited"
    if not sender.is_demo:
        # Preferences precede credentials. Lock both mailboxes in one global
        # order so reciprocal reminders can refresh tokens without deadlocking.
        recipient = db.scalar(select(User).where(User.email == thread.recipient_email))
        if recipient:
            db.scalar(
                select(RecipientPreferences)
                .where(
                    RecipientPreferences.user_id == recipient.id,
                )
                .with_for_update()
            )
        user_ids = [sender.id] + ([recipient.id] if recipient else [])
        list(
            db.scalars(
                select(ConnectedAccount)
                .where(
                    ConnectedAccount.user_id.in_(user_ids),
                )
                .order_by(ConnectedAccount.user_id)
                .with_for_update()
            )
        )
    if not verified_for_sender(db, thread, sender):
        return "unable_to_verify"
    if thread.has_reply:
        return "thread_replied"
    last = db.scalar(
        select(Reminder)
        .where(
            Reminder.sender_user_id == sender.id,
            Reminder.thread_id == thread.id,
            Reminder.status.in_(
                [
                    "delivered",
                    "dismissed",
                    "email_pending",
                    "email_sending",
                    "email_sent",
                    "email_unknown",
                ]
            ),
            Reminder.id != ignore_reminder_id if ignore_reminder_id else True,
        )
        .order_by(Reminder.created_at.desc())
        .limit(1)
    )
    if last and (last.delivered_at or last.created_at).replace(tzinfo=UTC) > now() - timedelta(
        hours=settings.cooldown_hours
    ):
        return "cooldown_active"
    recipient = db.scalar(select(User).where(User.email == thread.recipient_email))
    if not recipient:
        return email_fallback_status(db, thread, sender)
    prefs = preferences(db, recipient.id)
    if not prefs.reminders_enabled or sender.email in prefs.blocked_senders:
        return "recipient_disabled"
    if not sender.is_demo and prefs.notification_enabled:
        push_ready = get_settings().push_configured and db.scalar(
            select(PushSubscription.id)
            .where(
                PushSubscription.user_id == recipient.id,
                PushSubscription.active.is_(True),
            )
            .limit(1)
        )
        if not push_ready and not extension_ready(db, recipient.id):
            return email_fallback_status(db, thread, sender)
    account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == recipient.id))
    status = delivery_status(db, account, thread, sender)
    return (
        email_fallback_status(db, thread, sender) if status == "recipient_not_connected" else status
    )


def eligibility_view(db, thread, actor):
    status = eligibility(db, thread, actor)
    channel = "email" if status in {"email_ready", "email_permission_required"} else "in_app"
    recipient = db.scalar(select(User).where(User.email == thread.recipient_email))
    prefs = db.get(RecipientPreferences, recipient.id) if recipient else None
    subscribed = bool(
        recipient
        and prefs
        and prefs.reminders_enabled
        and prefs.notification_enabled
        and actor.email not in prefs.blocked_senders
        and (
            extension_ready(db, recipient.id)
            or (
                get_settings().push_configured
                and db.scalar(
                    select(PushSubscription.id)
                    .where(
                        PushSubscription.user_id == recipient.id,
                        PushSubscription.active.is_(True),
                    )
                    .limit(1)
                )
            )
        )
    )
    return {
        "status": status,
        "simulated": actor.is_demo,
        "delivery_channel": channel,
        "push_available": subscribed,
        "email_preview": reminder_text(actor) if channel == "email" else None,
    }


def create_reminder(db: Session, sender: User, thread_id: str, key: str, delivery_channel="in_app"):
    if db.get_bind().dialect.name == "sqlite":
        # SQLite lacks FOR UPDATE. Reserve its writer before checking cooldowns
        # so parallel requests in the local demo cannot both pass eligibility.
        db.execute(text("BEGIN IMMEDIATE"))
    # Serialize requests from a sender in Postgres to protect cooldown and daily quota.
    # NO KEY UPDATE serializes senders while allowing foreign-key checks from
    # a concurrent reminder sent back by the recipient.
    db.scalar(select(User).where(User.id == sender.id).with_for_update(key_share=True))
    previous = db.scalar(
        select(Reminder).where(
            Reminder.sender_user_id == sender.id,
            Reminder.idempotency_key == key,
        )
    )
    if previous:
        if previous.thread_id != thread_id or previous.delivery_channel != delivery_channel:
            raise HTTPException(409, "Idempotency key already used for another conversation")
        return previous
    thread = db.get(EmailThread, thread_id)
    if not thread or thread.sender_user_id != sender.id:
        raise HTTPException(404, "Conversation not found")
    # Global lock order: sender, then recipient preference. Preference updates use
    # the same row lock, so disabling and delivering have a defined order.
    recipient = db.scalar(select(User).where(User.email == thread.recipient_email))
    if recipient:
        db.scalar(
            select(RecipientPreferences)
            .where(
                RecipientPreferences.user_id == recipient.id,
            )
            .with_for_update()
        )
    status = eligibility(db, thread, sender)
    if status == "rate_limited":
        # Avoid unbounded failure records once the request budget is exhausted.
        raise HTTPException(429, "Daily reminder limit reached", headers={"Retry-After": "86400"})
    if status in {"email_ready", "email_permission_required"} and delivery_channel != "email":
        raise HTTPException(
            409, "This recipient needs an email reminder. Review and confirm email delivery first."
        )
    if status == "delivered" and delivery_channel == "email":
        raise HTTPException(
            409, "The recipient is now connected. Review the in-app delivery confirmation again."
        )
    if status == "email_ready":
        status = "email_pending"
    reminder = Reminder(
        thread_id=thread.id,
        sender_user_id=sender.id,
        recipient_user_id=recipient.id if recipient else None,
        idempotency_key=key,
        status=status,
        delivered_at=now() if status == "delivered" else None,
        delivery_channel=delivery_channel,
        notification_status="queued" if status == "delivered" and not sender.is_demo else "none",
    )
    db.add(reminder)
    db.flush()
    db.add(
        AuditLog(
            user_id=sender.id,
            action="reminder.requested",
            resource_id=reminder.id,
            details={"status": status, "provider": thread.verification_source},
        )
    )
    db.commit()
    return reminder


def reminder_view(db: Session, value: Reminder, actor: User):
    thread = db.get(EmailThread, value.thread_id)
    sender = db.get(User, value.sender_user_id)
    recipient = db.scalar(select(User).where(User.email == thread.recipient_email))
    return {
        "id": value.id,
        "thread_id": thread.id,
        "subject": thread.subject,
        "preview": thread.preview,
        "sender_email": sender.email,
        "sender_name": sender.display_name,
        "recipient_email": thread.recipient_email,
        "recipient_name": recipient.display_name
        if recipient
        else thread.recipient_email.split("@")[0].title(),
        "status": value.status,
        "created_at": value.created_at,
        "delivered_at": value.delivered_at,
        "dismissed_at": value.dismissed_at,
        "direction": "sent" if value.sender_user_id == actor.id else "received",
        "simulated": thread.verification_source == "demo",
        "provider_thread_id": thread.recipient_provider_thread_id
        if value.recipient_user_id == actor.id
        else thread.provider_thread_id,
        "delivery_channel": value.delivery_channel,
        "notification_status": value.notification_status,
    }
