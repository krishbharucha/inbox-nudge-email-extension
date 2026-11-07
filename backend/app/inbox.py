"""Read-only Gmail inbox summaries. Message data is returned, never persisted."""

import re
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import getaddresses

from fastapi import APIRouter, HTTPException, Query, Response

from .auth import Actor, Db
from .gmail import PROVIDER_ID, account_for, headers
from .google_http import GmailClient, GoogleUnavailable

router = APIRouter(prefix="/api/inbox")


def readable_header(value):
    try:
        return str(make_header(decode_header(value)))
    except (ValueError, UnicodeError, LookupError):
        return value


def inbox_summary(body, own_email):
    messages = [
        m
        for m in body.get("messages", [])
        if not {"DRAFT", "TRASH", "SPAM"} & set(m.get("labelIds", []))
    ]
    if not messages or not any("INBOX" in m.get("labelIds", []) for m in messages):
        return None
    latest = max(messages, key=lambda m: int(m.get("internalDate", 0)))
    meta = headers(latest)
    senders = getaddresses([readable_header(meta.get("from", ""))])
    name, email = senders[0] if senders else ("", "")
    email = email.lower()
    timestamp = int(latest.get("internalDate", 0))
    if timestamp <= 0:
        raise ValueError("Invalid message date")
    return {
        "provider_thread_id": body["id"],
        "sender_name": "Me" if email == own_email else name or email or "Unknown sender",
        "sender_email": email,
        "subject": readable_header(meta.get("subject", "(No subject)")),
        "last_message_at": datetime.fromtimestamp(timestamp / 1000, UTC),
        "unread": any("UNREAD" in m.get("labelIds", []) for m in messages),
        "message_count": len(messages),
    }


@router.get("/gmail")
def gmail_inbox(
    actor: Actor,
    db: Db,
    response: Response,
    page_token: str | None = Query(default=None, max_length=1000),
):
    if actor.is_demo:
        raise HTTPException(403, "Connect Google to view your real Gmail inbox")
    response.headers["Cache-Control"] = "no-store"
    try:
        client = GmailClient(db, account_for(db, actor.id))
        params = {"labelIds": "INBOX", "maxResults": 10}
        if page_token:
            params["pageToken"] = page_token
        page = client.get("/threads", params)
        result = []
        for item in page.get("threads", []):
            provider_id = item.get("id", "")
            if not re.fullmatch(PROVIDER_ID, provider_id):
                raise GoogleUnavailable("Google returned an invalid inbox reference")
            body = client.thread(provider_id)
            if body.get("id") != provider_id:
                raise GoogleUnavailable("Google returned a different inbox conversation")
            summary = inbox_summary(body, actor.email)
            if summary:
                result.append(summary)
        # Persist only refreshed credentials, not inbox subjects or messages.
        db.commit()
        return {"threads": result, "next_page_token": page.get("nextPageToken")}
    except (GoogleUnavailable, ValueError, TypeError, KeyError, OverflowError):
        raise HTTPException(
            503,
            "Unable to load your Gmail inbox. Try refreshing, or reconnect Google in Preferences.",
        ) from None
