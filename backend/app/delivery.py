"""Delivery to the managed inbox, verified against both participants' mailboxes."""

from .config import get_settings
from .gmail import match_recipient, verify_sender
from .models import ConnectedAccount, EmailThread, User


def verified_for_sender(db, thread: EmailThread, sender: User):
    if sender.is_demo:
        return get_settings().demo_mode and thread.verification_source == "demo"
    return thread.verification_source == "gmail" and verify_sender(db, thread, sender)


def delivery_status(db, account: ConnectedAccount | None, thread: EmailThread, sender: User):
    if not account or account.connection_status != "connected":
        return "recipient_not_connected"
    if sender.is_demo:
        return (
            "delivered"
            if account.provider == "demo"
            and get_settings().demo_mode
            and thread.recipient_provider_thread_id
            else "recipient_not_connected"
        )
    if account.provider != "google":
        return "recipient_not_connected"
    return match_recipient(db, thread, sender, account)
