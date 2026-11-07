"""Durable delivery jobs. Ambiguous email sends are reconciled, never retried."""

import asyncio
import json
import logging
from datetime import timedelta

from pywebpush import WebPushException, webpush
from requests.exceptions import RequestException
from sqlalchemy import select, update

from .config import get_settings
from .db import SessionLocal
from .email_delivery import encode_email, send_email
from .extension_delivery import extension_ready
from .gmail import account_for
from .google_http import GMAIL_SEND_SCOPE, GmailClient, GoogleUnavailable, decrypt
from .models import (
    AuditLog,
    EmailThread,
    PushSubscription,
    RecipientPreferences,
    Reminder,
    User,
    now,
)
from .push import PushHTTPSession, endpoint_allowed
from .reminders import eligibility

logger = logging.getLogger(__name__)


def dispatch_email(reminder_id):
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text

            db.execute(text("BEGIN IMMEDIATE"))
        value = db.get(Reminder, reminder_id)
        if not value or value.status != "email_pending":
            return
        sender = db.scalar(
            select(User).where(User.id == value.sender_user_id).with_for_update(key_share=True)
        )
        # Reload after acquiring the sender lock: another worker may have sent it.
        db.refresh(value)
        if value.status != "email_pending":
            return
        thread = db.get(EmailThread, value.thread_id)
        try:
            status = eligibility(db, thread, sender, ignore_reminder_id=value.id)
            if status != "email_ready":
                value.status = status if status != "delivered" else "unable_to_verify"
                db.commit()
                return
            account = account_for(db, sender.id)
            access_token = GmailClient(db, account).access_token()
            if GMAIL_SEND_SCOPE not in account.granted_scopes:
                value.status = "email_permission_required"
                db.commit()
                return
            value.outbound_message_id = f"<nudge.{value.id}@{sender.email.rsplit('@', 1)[1]}>"
            raw = encode_email(db, value, sender, thread)
            thread_id = thread.provider_thread_id
            value.status, value.attempted_at = "email_sending", now()
            # Commit the irreversible-send boundary before contacting Gmail.
            db.commit()
        except (GoogleUnavailable, ValueError, TypeError):
            value.status = "email_failed"
            db.commit()
            return
    # This is the only call site for Gmail's messages.send endpoint.
    status, provider_id = send_email(access_token, thread_id, raw)
    with SessionLocal() as db:
        value = db.get(Reminder, reminder_id)
        if not value or value.status != "email_sending":
            return
        value.status, value.provider_message_id = status, provider_id
        if status == "email_sent":
            value.delivered_at = now()
        db.add(
            AuditLog(
                user_id=value.sender_user_id,
                action="reminder.email_result",
                resource_id=value.id,
                details={"status": status},
            )
        )
        db.commit()


def dispatch_push(reminder_id):
    with SessionLocal() as db:
        claimed = db.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id, Reminder.notification_status == "queued")
            .values(notification_status="sending", attempted_at=now())
        )
        if claimed.rowcount != 1:
            return
        db.commit()
        value = db.get(Reminder, reminder_id)
        prefs = (
            db.get(RecipientPreferences, value.recipient_user_id)
            if value.recipient_user_id
            else None
        )
        sender = db.get(User, value.sender_user_id)
        if (
            value.status != "delivered"
            or not prefs
            or not prefs.reminders_enabled
            or not prefs.notification_enabled
            or sender.email in prefs.blocked_senders
        ):
            value.notification_status = "suppressed"
            db.commit()
            return
        settings = get_settings()
        if extension_ready(db, value.recipient_user_id):
            db.execute(
                update(Reminder)
                .where(Reminder.id == value.id, Reminder.notification_status == "sending")
                .values(notification_status="extension_pending")
            )
            db.commit()
            return
        subscriptions = db.scalars(
            select(PushSubscription).where(
                PushSubscription.user_id == value.recipient_user_id,
                PushSubscription.active.is_(True),
            )
        ).all()
        if not settings.push_configured or not subscriptions:
            value.notification_status = "no_subscription"
            db.commit()
            return
        thread = db.get(EmailThread, value.thread_id)
        payload = json.dumps(
            {
                "title": "Inbox Nudge",
                "body": f"{sender.display_name[:80]} reminded you about {thread.subject[:160]}",
                "tag": f"nudge-{value.id}",
                "url": "/?view=priority",
            }
        )
        targets = [(item.id, item.subscription_encrypted) for item in subscriptions]
    accepted, expired = 0, []
    for subscription_id, encrypted in targets:
        try:
            subscription = json.loads(decrypt(encrypted))
            if not endpoint_allowed(subscription["endpoint"]):
                continue
            with PushHTTPSession() as http:
                response = webpush(
                    subscription_info=subscription,
                    data=payload,
                    vapid_private_key=get_settings().vapid_private_key.get_secret_value(),
                    vapid_claims={"sub": get_settings().vapid_subject},
                    ttl=3600,
                    timeout=10,
                    requests_session=http,
                )
            if 200 <= response.status_code < 300:
                accepted += 1
        except WebPushException as error:
            response = error.response
            if response is not None and response.status_code in {404, 410}:
                expired.append(subscription_id)
        except (GoogleUnavailable, ValueError, TypeError, KeyError, OSError, RequestException):
            continue
    with SessionLocal() as db:
        value = db.get(Reminder, reminder_id)
        if value:
            value.notification_status = "push_accepted" if accepted else "failed"
        for subscription_id in expired:
            subscription = db.get(PushSubscription, subscription_id)
            if subscription:
                subscription.active = False
        db.commit()


def dispatch_one(reminder_id):
    try:
        with SessionLocal() as db:
            value = db.get(Reminder, reminder_id)
            email = bool(value and value.status == "email_pending")
            push = bool(value and value.notification_status == "queued")
        if email:
            dispatch_email(reminder_id)
        elif push:
            dispatch_push(reminder_id)
    except Exception as failure:
        # Never log exception values: HTTP libraries may include private URLs.
        logger.warning("Reminder delivery stopped (%s)", type(failure).__name__)


def drain():
    with SessionLocal() as db:
        # A crash after crossing the send boundary is ambiguous, not retryable.
        db.execute(
            update(Reminder)
            .where(
                Reminder.status == "email_sending",
                Reminder.attempted_at < now() - timedelta(minutes=5),
            )
            .values(status="email_unknown")
        )
        db.execute(
            update(Reminder)
            .where(
                Reminder.notification_status == "sending",
                Reminder.attempted_at < now() - timedelta(minutes=5),
            )
            .values(notification_status="unknown")
        )
        ids = db.scalars(
            select(Reminder.id)
            .where(
                (Reminder.status == "email_pending") | (Reminder.notification_status == "queued")
            )
            .order_by(Reminder.created_at)
            .limit(10)
        ).all()
        db.commit()
    for reminder_id in ids:
        dispatch_one(reminder_id)


async def delivery_loop(stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.to_thread(drain)
        except Exception as failure:
            logger.warning("Delivery sweep stopped (%s)", type(failure).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass
