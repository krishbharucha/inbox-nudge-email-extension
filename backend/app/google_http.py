"""Small fixed-host Google clients. Provider secrets never leave the backend."""

from datetime import UTC, timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings
from .models import ConnectedAccount, now

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
SCOPES = ["openid", "email", "profile", GMAIL_SCOPE]
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GoogleUnavailable(Exception):
    pass


def encrypt(value: str):
    return (
        Fernet(get_settings().token_encryption_key.get_secret_value().encode())
        .encrypt(value.encode())
        .decode()
    )


def decrypt(value: str):
    try:
        return (
            Fernet(get_settings().token_encryption_key.get_secret_value().encode())
            .decrypt(value.encode())
            .decode()
        )
    except (ValueError, InvalidToken):
        raise GoogleUnavailable("Stored authorization is unavailable; reconnect Google") from None


def google_request(method: str, url: str, **kwargs):
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError:
        raise GoogleUnavailable("Google is temporarily unavailable") from None
    return response


def token_request(data: dict):
    settings = get_settings()
    response = google_request(
        "POST",
        TOKEN_URL,
        data={
            **data,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise GoogleUnavailable("Google authorization failed; reconnect your account")
    try:
        body = response.json()
        if not isinstance(body.get("access_token"), str) or not body["access_token"]:
            raise ValueError()
        return body
    except (ValueError, TypeError):
        raise GoogleUnavailable("Google returned an invalid token response") from None


class GmailClient:
    def __init__(self, db, account: ConnectedAccount):
        self.db, self.account = db, account

    def access_token(self):
        account = self.account
        if account.connection_status != "connected" or GMAIL_SCOPE not in account.granted_scopes:
            raise GoogleUnavailable("Google account is not connected")
        if (
            account.access_token_encrypted
            and account.token_expires_at
            and account.token_expires_at.replace(tzinfo=UTC) > now() + timedelta(seconds=60)
        ):
            return decrypt(account.access_token_encrypted)
        if not account.refresh_token_encrypted:
            raise GoogleUnavailable("Reconnect Google to renew mailbox access")
        body = token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": decrypt(account.refresh_token_encrypted),
            }
        )
        if "scope" in body and GMAIL_SCOPE not in body["scope"].split():
            raise GoogleUnavailable("Gmail read-only access was not granted")
        if "scope" in body:
            account.granted_scopes = body["scope"].split()
        account.access_token_encrypted = encrypt(body["access_token"])
        if body.get("refresh_token"):
            account.refresh_token_encrypted = encrypt(body["refresh_token"])
        account.token_expires_at = now() + timedelta(seconds=int(body.get("expires_in", 3600)))
        self.db.flush()
        return body["access_token"]

    def get(self, path: str, params=None):
        response = google_request(
            "GET",
            f"{GMAIL_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        if response.status_code != 200:
            raise GoogleUnavailable("Unable to verify this Gmail conversation")
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except ValueError:
            raise GoogleUnavailable("Google returned an invalid mailbox response") from None

    def thread(self, thread_id: str):
        headers = ["From", "To", "Cc", "Bcc", "Subject", "Message-ID"]
        return self.get(
            f"/threads/{thread_id}",
            [("format", "metadata"), *[("metadataHeaders", h) for h in headers]],
        )
