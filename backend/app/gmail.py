"""Verified Gmail metadata import and recipient-scoped thread resolution."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import getaddresses

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import select

from .auth import Actor, Db
from .google_http import GmailClient, GoogleUnavailable
from .models import AuditLog, ConnectedAccount, EmailThread

router = APIRouter(prefix="/api/threads")
PROVIDER_ID = r"^[a-fA-F0-9]{10,32}$"
MESSAGE_ID = re.compile(r"<[A-Za-z0-9.!#$%&'*+\-/=?^_`{|}~]+@[A-Za-z0-9.-]+>")


def headers(message):
    result = {}
    for item in message.get("payload", {}).get("headers", []):
        name = item.get("name", "").lower()
        if name in result:
            raise ValueError("Ambiguous message headers")
        result[name] = item.get("value", "")
    return result


def addresses(value):
    return {email.lower() for _, email in getaddresses([value]) if email and "@" in email}


def recipient_addresses(meta):
    return set().union(*(addresses(meta[name]) for name in ("to", "cc", "bcc") if meta.get(name)))


@dataclass
class VerifiedThread:
    provider_thread_id: str
    recipient_email: str
    subject: str
    original_message_id: str
    last_message_at: datetime
    has_reply: bool


def inspect_sent_thread(body: dict, sender_email: str):
    sender_email = sender_email.lower()
    messages = [m for m in body.get("messages", []) if "DRAFT" not in m.get("labelIds", [])]
    if not messages or not re.fullmatch(PROVIDER_ID, body.get("id", "")):
        raise ValueError("Unable to verify the conversation")
    messages.sort(key=lambda m: int(m.get("internalDate", 0)))
    sent = [
        m
        for m in messages
        if "SENT" in m.get("labelIds", [])
        and addresses(headers(m).get("from", "")) == {sender_email}
    ]
    if not sent or sent[0] != messages[0]:
        raise ValueError("Only conversations started by this sender are eligible")
    original = headers(sent[0])
    recipients = recipient_addresses(original) - {sender_email}
    if len(recipients) != 1:
        raise ValueError("Only conversations with a single recipient are supported")
    message_id = original.get("message-id", "").strip()
    if len(message_id) > 998 or not MESSAGE_ID.fullmatch(message_id):
        raise ValueError("Unable to verify the original Message-ID")
    for message in sent:
        meta = headers(message)
        if recipient_addresses(meta) - {sender_email} != recipients:
            raise ValueError("Conversation recipients changed")
    has_reply = any(addresses(headers(m).get("from", "")) != {sender_email} for m in messages)
    last_ms = int(messages[-1].get("internalDate", 0))
    if last_ms <= 0:
        raise ValueError("Unable to verify the message date")
    return VerifiedThread(
        provider_thread_id=body["id"],
        recipient_email=next(iter(recipients)),
        subject=original.get("subject", "(No subject)")[:500],
        original_message_id=message_id,
        last_message_at=datetime.fromtimestamp(last_ms / 1000, UTC),
        has_reply=has_reply,
    )


def account_for(db, user_id):
    value = db.scalar(
        select(ConnectedAccount).where(ConnectedAccount.user_id == user_id).with_for_update()
    )
    if not value or value.provider != "google" or value.connection_status != "connected":
        raise GoogleUnavailable("Connect your Google account first")
    return value


def store_thread(db, actor, verified: VerifiedThread):
    value = db.scalar(
        select(EmailThread).where(
            EmailThread.sender_user_id == actor.id,
            EmailThread.provider_thread_id == verified.provider_thread_id,
        )
    )
    if not value:
        value = EmailThread(sender_user_id=actor.id, provider_thread_id=verified.provider_thread_id)
        db.add(value)
    elif value.original_message_id and value.original_message_id != verified.original_message_id:
        raise ValueError("The conversation origin changed")
    if value.recipient_email != verified.recipient_email:
        value.recipient_provider_thread_id = None
    value.recipient_email, value.subject = verified.recipient_email, verified.subject
    value.original_message_id = verified.original_message_id
    value.last_message_at, value.has_reply = verified.last_message_at, verified.has_reply
    value.verification_source, value.preview = "gmail", ""
    db.flush()
    return value


def verify_sender(db, thread, actor):
    try:
        client = GmailClient(db, account_for(db, actor.id))
        verified = inspect_sent_thread(client.thread(thread.provider_thread_id), actor.email)
        store_thread(db, actor, verified)
        return True
    except (GoogleUnavailable, ValueError, TypeError, KeyError, OverflowError):
        return False


def match_recipient(db, thread, sender, account):
    """No recipient-side match, no delivery. Never infer a shared Gmail thread ID."""
    try:
        if not thread.original_message_id or not MESSAGE_ID.fullmatch(thread.original_message_id):
            return "unable_to_verify"
        client = GmailClient(db, account)
        found = client.get(
            "/messages", {"q": f"rfc822msgid:{thread.original_message_id}", "maxResults": 2}
        )
        candidates = found.get("messages", [])
        if len(candidates) != 1:
            return "unable_to_verify"
        recipient_thread_id = candidates[0].get("threadId", "")
        if not re.fullmatch(PROVIDER_ID, recipient_thread_id):
            return "unable_to_verify"
        body = client.thread(recipient_thread_id)
        if body.get("id") != recipient_thread_id:
            return "unable_to_verify"
        originals = [
            m
            for m in body.get("messages", [])
            if headers(m).get("message-id", "").strip() == thread.original_message_id
        ]
        if len(originals) != 1:
            return "unable_to_verify"
        original = originals[0]
        meta = headers(original)
        if addresses(meta.get("from", "")) != {
            sender.email
        } or thread.recipient_email not in recipient_addresses(meta):
            return "unable_to_verify"
        if {"DRAFT", "SPAM", "TRASH"} & set(original.get("labelIds", [])):
            return "unable_to_verify"
        original_time = int(original.get("internalDate", 0))
        if original_time <= 0:
            return "unable_to_verify"
        for message in body.get("messages", []):
            if "DRAFT" in message.get("labelIds", []):
                continue
            if int(message.get("internalDate", 0)) >= original_time and addresses(
                headers(message).get("from", "")
            ) != {sender.email}:
                thread.has_reply = True
                return "thread_replied"
        thread.recipient_provider_thread_id = recipient_thread_id
        return "delivered"
    except (GoogleUnavailable, ValueError, TypeError, KeyError):
        return "unable_to_verify"


class ThreadImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_thread_id: str | None = Field(default=None, pattern=PROVIDER_ID)
    provider_message_id: str | None = Field(default=None, pattern=PROVIDER_ID)

    @model_validator(mode="after")
    def exactly_one(self):
        if bool(self.provider_thread_id) == bool(self.provider_message_id):
            raise ValueError("Provide exactly one Gmail thread or message ID")
        return self


@router.post("/import")
def import_thread(body: ThreadImport, actor: Actor, db: Db):
    if actor.is_demo:
        raise HTTPException(403, "Demo accounts cannot access real Gmail")
    try:
        client = GmailClient(db, account_for(db, actor.id))
        provider_id = body.provider_thread_id
        if body.provider_message_id:
            provider_id = client.get(
                f"/messages/{body.provider_message_id}",
                {"format": "metadata", "fields": "threadId"},
            ).get("threadId", "")
        if not re.fullmatch(PROVIDER_ID, provider_id or ""):
            raise ValueError("Unable to resolve the Gmail thread")
        verified = inspect_sent_thread(client.thread(provider_id), actor.email)
        value = store_thread(db, actor, verified)
        db.add(AuditLog(user_id=actor.id, action="thread.verified", resource_id=value.id))
        db.commit()
        return {
            "id": value.id,
            "subject": value.subject,
            "recipient_email": value.recipient_email,
            "preview": "",
            "has_reply": value.has_reply,
            "last_message_at": value.last_message_at,
            "eligibility": "thread_replied" if value.has_reply else "unable_to_verify",
        }
    except GoogleUnavailable:
        raise HTTPException(
            503, "Unable to verify this conversation with Gmail. Reconnect or try again."
        ) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(
            422, "Only verified, single-recipient conversations started by you can be nudged."
        ) from None


class ThreadResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=500)
    recipient_email: EmailStr


@router.post("/resolve")
def resolve_thread(body: ThreadResolve, actor: Actor, db: Db):
    """DOM hints narrow a search; provider metadata and a unique match authorize it."""
    if actor.is_demo:
        raise HTTPException(403, "Connect Gmail to verify a conversation")
    from email.header import decode_header, make_header

    def normalize(subject):
        return " ".join(str(make_header(decode_header(subject))).split())

    def quoted(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    try:
        client = GmailClient(db, account_for(db, actor.id))
        result = client.get(
            "/threads",
            {
                "q": f"in:sent to:{quoted(str(body.recipient_email))} subject:{quoted(body.subject)}",
                "maxResults": 20,
            },
        )
        if result.get("nextPageToken"):
            raise ValueError("Too many possible conversations")
        candidates = result.get("threads", [])
        if not isinstance(candidates, list) or len(candidates) > 20:
            raise ValueError("Invalid conversation results")
        matches = {}
        for item in candidates:
            provider_id = item.get("id", "")
            if not re.fullmatch(PROVIDER_ID, provider_id):
                raise ValueError("Invalid conversation reference")
            try:
                verified = inspect_sent_thread(client.thread(provider_id), actor.email)
            except ValueError:
                continue
            if (
                normalize(verified.subject) == normalize(body.subject)
                and verified.recipient_email == str(body.recipient_email).lower()
            ):
                matches[verified.provider_thread_id] = verified
        if len(matches) != 1:
            raise ValueError("No unique matching conversation")
        value = store_thread(db, actor, next(iter(matches.values())))
        db.add(AuditLog(user_id=actor.id, action="thread.verified", resource_id=value.id))
        db.commit()
        return {"id": value.id, "subject": value.subject, "recipient_email": value.recipient_email}
    except GoogleUnavailable:
        raise HTTPException(
            503, "Gmail could not verify this conversation. Try again shortly."
        ) from None
    except (ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(
            409,
            "Gmail could not identify one matching conversation. Reload Gmail and reopen the sent email.",
        ) from None


class ThreadSync(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_token: str | None = Field(default=None, max_length=1000)


@router.post("/sync")
def sync_threads(body: ThreadSync, actor: Actor, db: Db):
    if actor.is_demo:
        raise HTTPException(403, "Demo accounts cannot access real Gmail")
    try:
        client = GmailClient(db, account_for(db, actor.id))
        params = {"labelIds": "SENT", "maxResults": 10}
        if body.page_token:
            params["pageToken"] = body.page_token
        page = client.get("/threads", params)
        synced, skipped = 0, 0
        for item in page.get("threads", []):
            try:
                if not re.fullmatch(PROVIDER_ID, item.get("id", "")):
                    raise ValueError()
                verified = inspect_sent_thread(client.thread(item["id"]), actor.email)
                store_thread(db, actor, verified)
                synced += 1
            except (ValueError, TypeError, KeyError, OverflowError):
                skipped += 1
        db.add(
            AuditLog(
                user_id=actor.id,
                action="threads.synced",
                resource_id=actor.id,
                details={"synced": synced, "skipped": skipped},
            )
        )
        db.commit()
        return {"synced": synced, "skipped": skipped, "next_page_token": page.get("nextPageToken")}
    except GoogleUnavailable:
        raise HTTPException(
            503, "Unable to sync Gmail. Reconnect your Google account or try again."
        ) from None
