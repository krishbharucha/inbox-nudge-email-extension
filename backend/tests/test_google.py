import secrets
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app import google_http, google_oauth
from app.auth import issue_token
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.google_http import GMAIL_BASE, GMAIL_SCOPE, TOKEN_URL, GoogleUnavailable, encrypt
from app.models import (
    ConnectedAccount,
    EmailThread,
    OAuthAttempt,
    PushSubscription,
    RecipientPreferences,
    User,
    now,
)

SENDER_ID = "1111111111111111"
RECIPIENT_ID = "2222222222222222"
INBOX_ID = "3333333333333333"
RFC_ID = "<original.123@mail.gmail.com>"


@pytest.fixture
def google_settings(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("client-secret-only-on-server"))
    monkeypatch.setattr(settings, "token_encryption_key", SecretStr(Fernet.generate_key().decode()))
    return settings


def message(*, recipient_copy=False, reply=False):
    return {
        "id": "aaaaaaaaaaaaaaaa",
        "threadId": RECIPIENT_ID if recipient_copy else SENDER_ID,
        "internalDate": str(
            int((now() - timedelta(days=2 if not reply else 1)).timestamp() * 1000)
        ),
        "labelIds": ["SENT"] if not recipient_copy or reply else ["INBOX"],
        "payload": {
            "headers": [
                {
                    "name": "From",
                    "value": "recipient@gmail.com" if reply else "Sender <sender@gmail.com>",
                },
                {
                    "name": "To",
                    "value": "sender@gmail.com" if reply else "Recipient <recipient@gmail.com>",
                },
                {"name": "Subject", "value": "Project check-in"},
                {"name": "Message-ID", "value": "<reply@mail.gmail.com>" if reply else RFC_ID},
            ]
        },
        "snippet": "This body snippet must never be stored.",
    }


@pytest.fixture
def gmail_accounts(client, google_settings, monkeypatch):
    monkeypatch.setattr(google_settings, "vapid_private_key", SecretStr("mock-only-private-key"))
    monkeypatch.setattr(google_settings, "vapid_public_key", "mock-only-public-key")
    with SessionLocal() as db:
        for id_, email in [("sender", "sender@gmail.com"), ("recipient", "recipient@gmail.com")]:
            user = User(id=id_, email=email, display_name=id_.title(), is_demo=False)
            db.add(user)
            db.flush()
            db.add(
                ConnectedAccount(
                    user_id=id_,
                    provider="google",
                    provider_account_id=f"google-{id_}",
                    connection_status="connected",
                    access_token_encrypted=encrypt(f"access-{id_}"),
                    refresh_token_encrypted=encrypt(f"refresh-{id_}"),
                    token_expires_at=now() + timedelta(hours=1),
                    granted_scopes=[GMAIL_SCOPE],
                )
            )
            db.add(RecipientPreferences(user_id=id_))
        db.add(
            PushSubscription(
                user_id="recipient",
                endpoint_hash="a" * 64,
                subscription_encrypted=encrypt(
                    '{"endpoint":"https://fcm.googleapis.com/fcm/send/mock"}'
                ),
            )
        )
        db.commit()
        sender_headers = {"Authorization": f"Bearer {issue_token(db.get(User, 'sender'))}"}
        recipient_headers = {"Authorization": f"Bearer {issue_token(db.get(User, 'recipient'))}"}
    state = {
        "sender": {"id": SENDER_ID, "messages": [message()]},
        "recipient": {"id": RECIPIENT_ID, "messages": [message(recipient_copy=True)]},
        "matches": [{"id": "aaaaaaaaaaaaaaaa", "threadId": RECIPIENT_ID}],
        "calls": [],
        "inbox": {"id": INBOX_ID, "messages": [message(recipient_copy=True)]},
        "inbox_ids": [{"id": INBOX_ID}],
    }
    state["inbox"]["messages"][0]["labelIds"] = ["INBOX", "UNREAD"]
    state["inbox"]["messages"][0]["payload"]["headers"][0]["value"] = (
        "=?utf-8?b?TWF5YSBDaGVu?= <friend@gmail.com>"
    )

    def mock_http(method, url, **kwargs):
        state["calls"].append((method, url, kwargs))
        token = kwargs.get("headers", {}).get("Authorization", "")
        if method == "POST" and url == google_http.REVOKE_URL:
            return httpx.Response(200)
        if method == "POST" and url == TOKEN_URL:
            return httpx.Response(
                200,
                json={"access_token": "renewed-token", "expires_in": 3600, "scope": GMAIL_SCOPE},
            )
        if url == f"{GMAIL_BASE}/threads/{SENDER_ID}" and token in {
            "Bearer access-sender",
            "Bearer renewed-token",
        }:
            return httpx.Response(200, json=deepcopy(state["sender"]))
        if url == f"{GMAIL_BASE}/threads/{RECIPIENT_ID}" and token == "Bearer access-recipient":
            return httpx.Response(200, json=deepcopy(state["recipient"]))
        if url == f"{GMAIL_BASE}/threads/{INBOX_ID}" and token == "Bearer access-sender":
            return httpx.Response(
                503 if state.get("inbox_failure") else 200, json=deepcopy(state["inbox"])
            )
        if url == f"{GMAIL_BASE}/messages" and token == "Bearer access-recipient":
            assert kwargs["params"]["q"] == f"rfc822msgid:{RFC_ID}"
            return httpx.Response(200, json={"messages": deepcopy(state["matches"])})
        if url == f"{GMAIL_BASE}/threads" and token == "Bearer access-sender":
            if kwargs.get("params", {}).get("q"):
                return httpx.Response(200, json={"threads": [{"id": SENDER_ID}]})
            if kwargs.get("params", {}).get("labelIds") == "INBOX":
                return httpx.Response(
                    200,
                    json={
                        "threads": deepcopy(state["inbox_ids"]),
                        "nextPageToken": "inbox-older" if state["inbox_ids"] else None,
                    },
                )
            return httpx.Response(
                200, json={"threads": [{"id": SENDER_ID}], "nextPageToken": "older-page"}
            )
        return httpx.Response(404, json={})

    monkeypatch.setattr(google_http, "google_request", mock_http)
    monkeypatch.setattr(google_oauth, "google_request", mock_http)
    return sender_headers, recipient_headers, state


def imported(client, sender):
    result = client.post(
        "/api/threads/import", headers=sender, json={"provider_thread_id": SENDER_ID}
    )
    assert result.status_code == 200, result.text
    return result.json()["id"]


def nudge(client, sender, id_):
    return client.post(
        "/api/reminders",
        headers=sender,
        json={"thread_id": id_, "idempotency_key": secrets.token_hex(16)},
    )


def test_inbox_requires_real_authenticated_account(client, alex):
    assert client.get("/api/inbox/gmail").status_code == 401
    assert client.get("/api/inbox/gmail", headers=alex).status_code == 403


def test_real_inbox_reads_headers_without_storing_mail(client, gmail_accounts):
    sender, _, state = gmail_accounts
    result = client.get("/api/inbox/gmail", headers=sender)
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    value = result.json()["threads"][0]
    assert value["provider_thread_id"] == INBOX_ID
    assert value["sender_name"] == "Maya Chen"
    assert value["sender_email"] == "friend@gmail.com"
    assert value["unread"] is True and value["message_count"] == 1
    assert result.json()["next_page_token"] == "inbox-older"
    assert "This body snippet must never be stored" not in result.text
    assert all(method == "GET" for method, _, _ in state["calls"])
    thread_calls = [
        kwargs for _, url, kwargs in state["calls"] if url.endswith(f"/threads/{INBOX_ID}")
    ]
    assert ("format", "metadata") in thread_calls[0]["params"]
    with SessionLocal() as db:
        assert db.scalar(select(EmailThread).where(EmailThread.sender_user_id == "sender")) is None


def test_inbox_paginates_and_preserves_empty_result(client, gmail_accounts):
    sender, _, state = gmail_accounts
    result = client.get("/api/inbox/gmail?page_token=next-token", headers=sender)
    assert result.status_code == 200
    params = state["calls"][0][2]["params"]
    assert params == {"labelIds": "INBOX", "maxResults": 10, "pageToken": "next-token"}
    state["inbox_ids"] = []
    assert client.get("/api/inbox/gmail", headers=sender).json() == {
        "threads": [],
        "next_page_token": None,
    }


def test_inbox_failure_is_not_reported_as_empty_mail(client, gmail_accounts):
    sender, _, state = gmail_accounts
    state["inbox_failure"] = True
    assert client.get("/api/inbox/gmail", headers=sender).status_code == 503


@pytest.mark.parametrize("labels", [["TRASH"], ["SPAM"], ["DRAFT"], ["SENT"]])
def test_inbox_does_not_show_removed_or_non_inbox_threads(client, gmail_accounts, labels):
    sender, _, state = gmail_accounts
    state["inbox"]["messages"][0]["labelIds"] = labels
    assert client.get("/api/inbox/gmail", headers=sender).json()["threads"] == []


def test_live_delivery_checks_both_mailboxes_and_stores_no_body(client, gmail_accounts):
    sender, recipient, state = gmail_accounts
    id_ = imported(client, sender)
    eligibility = client.get(f"/api/threads/{id_}/eligibility", headers=sender)
    assert eligibility.json() == {
        "status": "delivered",
        "simulated": False,
        "delivery_channel": "in_app",
        "push_available": True,
        "email_preview": None,
    }
    result = nudge(client, sender, id_)
    assert result.status_code == 201 and result.json()["status"] == "delivered"
    assert result.json()["simulated"] is False
    inbox = client.get("/api/inbox/priority", headers=recipient).json()
    assert inbox[0]["provider_thread_id"] == RECIPIENT_ID
    assert inbox[0]["preview"] == ""
    assert result.json()["provider_thread_id"] == SENDER_ID
    assert all(method == "GET" for method, _, _ in state["calls"])
    with SessionLocal() as db:
        value = db.get(EmailThread, id_)
        assert value.original_message_id == RFC_ID
        assert value.preview == "" and value.verification_source == "gmail"


def test_sender_reply_is_rechecked_at_submission(client, gmail_accounts):
    sender, _, state = gmail_accounts
    id_ = imported(client, sender)
    assert (
        client.get(f"/api/threads/{id_}/eligibility", headers=sender).json()["status"]
        == "delivered"
    )
    state["sender"]["messages"].append(message(reply=True))
    assert nudge(client, sender, id_).json()["status"] == "thread_replied"


def test_recipient_reply_is_checked_even_if_sender_copy_missing(client, gmail_accounts):
    sender, _, state = gmail_accounts
    id_ = imported(client, sender)
    state["recipient"]["messages"].append(message(recipient_copy=True, reply=True))
    assert nudge(client, sender, id_).json()["status"] == "thread_replied"


@pytest.mark.parametrize("change", ["no-match", "wrong-message-id", "trash", "ambiguous-match"])
def test_recipient_mapping_fails_closed(client, gmail_accounts, change):
    sender, _, state = gmail_accounts
    id_ = imported(client, sender)
    if change == "no-match":
        state["matches"] = []
    if change == "ambiguous-match":
        state["matches"] *= 2
    if change == "wrong-message-id":
        state["recipient"]["messages"][0]["payload"]["headers"][-1]["value"] = (
            "<other@mail.gmail.com>"
        )
    if change == "trash":
        state["recipient"]["messages"][0]["labelIds"] = ["TRASH"]
    result = nudge(client, sender, id_).json()
    assert result["status"] == "unable_to_verify" and result["delivered_at"] is None


def test_group_mail_spoofed_headers_and_extra_claims_rejected(client, gmail_accounts):
    sender, _, state = gmail_accounts
    state["sender"]["messages"][0]["payload"]["headers"].append(
        {"name": "Cc", "value": "third@gmail.com"}
    )
    assert (
        client.post(
            "/api/threads/import", headers=sender, json={"provider_thread_id": SENDER_ID}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/threads/import",
            headers=sender,
            json={"provider_thread_id": SENDER_ID, "recipient_email": "victim@gmail.com"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/threads/import", headers=sender, json={"provider_thread_id": "../../other"}
        ).status_code
        == 422
    )


def test_import_cannot_read_another_mailbox(client, gmail_accounts):
    _, recipient, _ = gmail_accounts
    assert (
        client.post(
            "/api/threads/import", headers=recipient, json={"provider_thread_id": SENDER_ID}
        ).status_code
        == 503
    )


def test_preferences_prevent_recipient_mailbox_access(client, gmail_accounts):
    sender, recipient, state = gmail_accounts
    id_ = imported(client, sender)
    client.put(
        "/api/preferences",
        headers=recipient,
        json={"reminders_enabled": False, "notification_enabled": True, "blocked_senders": []},
    )
    state["calls"].clear()
    assert nudge(client, sender, id_).json()["status"] == "recipient_disabled"
    assert not any(
        kwargs.get("headers", {}).get("Authorization") == "Bearer access-recipient"
        for _, _, kwargs in state["calls"]
    )


def test_live_sync_and_pagination(client, gmail_accounts):
    sender, _, _ = gmail_accounts
    result = client.post("/api/threads/sync", headers=sender, json={})
    assert result.json() == {"synced": 1, "skipped": 0, "next_page_token": "older-page"}
    assert len(client.get("/api/threads", headers=sender).json()) == 1


def test_refresh_tokens_are_used_only_on_backend(client, gmail_accounts):
    sender, _, state = gmail_accounts
    with SessionLocal() as db:
        account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == "sender"))
        account.token_expires_at = now() - timedelta(minutes=1)
        db.commit()
    imported(client, sender)
    refresh = [
        kwargs for method, url, kwargs in state["calls"] if method == "POST" and url == TOKEN_URL
    ]
    assert refresh[0]["data"]["refresh_token"] == "refresh-sender"
    with SessionLocal() as db:
        account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == "sender"))
        assert "renewed-token" not in account.access_token_encrypted
    assert "token" not in client.get("/api/me", headers=sender).text


def test_disconnect_revokes_credentials_and_all_app_sessions(client, gmail_accounts):
    sender, _, state = gmail_accounts
    result = client.post("/api/accounts/disconnect", headers=sender)
    assert result.json() == {"disconnected": True, "google_revoked": True}
    assert client.get("/api/me", headers=sender).status_code == 401
    with SessionLocal() as db:
        account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == "sender"))
        assert account.refresh_token_encrypted is None and account.access_token_encrypted is None
        assert account.connection_status == "disconnected"
        assert db.get(RecipientPreferences, "sender").reminders_enabled is False


def test_demo_cannot_import_live_mail(client, alex):
    assert (
        client.post(
            "/api/threads/import", headers=alex, json={"provider_thread_id": SENDER_ID}
        ).status_code
        == 403
    )
    assert client.post("/api/threads/sync", headers=alex, json={}).status_code == 403


def begin(client):
    verifier = secrets.token_urlsafe(48)
    result = client.get(
        "/api/auth/google/start",
        params={
            "redirect_uri": "http://127.0.0.1:5173/",
            "code_challenge": google_oauth.challenge(verifier),
        },
        follow_redirects=False,
    )
    assert result.status_code == 307
    return verifier, parse_qs(urlsplit(result.headers["location"]).query)


def test_google_setup_required(client):
    assert client.get("/api/health").json()["google_configured"] is False
    assert (
        client.get(
            "/api/auth/google/start",
            params={"redirect_uri": "http://127.0.0.1:5173/", "code_challenge": "x" * 43},
        ).status_code
        == 503
    )


def test_live_setup_rejects_public_demo_secret_without_exposing_credentials():
    secret = "private-google-secret-do-not-print"
    with pytest.raises(ValidationError) as caught:
        Settings(
            _env_file=None,
            jwt_secret="local-demo-only-change-this-secret-32chars",
            google_client_id="client.apps.googleusercontent.com",
            google_client_secret=secret,
            token_encryption_key=Fernet.generate_key().decode(),
        )
    assert "Replace the public demo" in str(caught.value)
    assert secret not in str(caught.value)


def test_oauth_redirect_allowlist_and_state_storage(client, google_settings):
    assert (
        client.get(
            "/api/auth/google/start",
            params={"redirect_uri": "https://attacker.example/", "code_challenge": "x" * 43},
        ).status_code
        == 400
    )
    _, query = begin(client)
    assert GMAIL_SCOPE in query["scope"][0] and "gmail.send" not in query["scope"][0]
    assert query["access_type"] == ["offline"]
    with SessionLocal() as db:
        attempt = db.get(OAuthAttempt, google_oauth.digest(query["state"][0]))
        assert attempt and attempt.state_hash != query["state"][0]
        assert query["code_challenge"][0] not in attempt.verifier_encrypted


def test_oauth_state_expiry_cancellation_and_replay(client, google_settings):
    _, query = begin(client)
    assert (
        client.get("/api/auth/google/callback?state=invalid", follow_redirects=False).status_code
        == 400
    )
    params = {"state": query["state"][0], "error": "access_denied"}
    result = client.get("/api/auth/google/callback", params=params, follow_redirects=False)
    assert "oauth_error=" in result.headers["location"]
    assert (
        client.get("/api/auth/google/callback", params=params, follow_redirects=False).status_code
        == 400
    )
    _, query = begin(client)
    with SessionLocal() as db:
        db.get(OAuthAttempt, google_oauth.digest(query["state"][0])).expires_at = now() - timedelta(
            seconds=1
        )
        db.commit()
    assert (
        client.get(
            "/api/auth/google/callback",
            params={"state": query["state"][0], "code": "expired"},
            follow_redirects=False,
        ).status_code
        == 400
    )


def mock_oauth(monkeypatch, query, *, profile_email="new.user@gmail.com", refresh=True):
    tokens = {
        "access_token": "google-access-secret",
        "scope": GMAIL_SCOPE,
        "expires_in": 3600,
        "id_token": "verified-id-token",
    }
    if refresh:
        tokens["refresh_token"] = "google-refresh-secret"
    monkeypatch.setattr(google_oauth, "token_request", lambda _: tokens)

    def identity(_, nonce):
        assert nonce == query["nonce"][0]
        return {"sub": "new-google-identity", "email": "new.user@gmail.com", "name": "New User"}

    monkeypatch.setattr(google_oauth, "verify_identity", identity)
    monkeypatch.setattr(
        google_oauth,
        "google_request",
        lambda *a, **k: httpx.Response(200, json={"emailAddress": profile_email}),
    )


def test_signing_certificate_error_does_not_leak_transport_details(
    client, google_settings, monkeypatch
):
    def unavailable(_):
        raise jwt.PyJWKClientConnectionError("private-token-and-transport-details")

    monkeypatch.setattr(google_oauth.keys, "get_signing_key_from_jwt", unavailable)
    with pytest.raises(GoogleUnavailable) as caught:
        google_oauth.verify_identity("not-logged", "nonce")
    assert "signing certificates" in str(caught.value)
    assert "private-token" not in str(caught.value)


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("SERVICE_DISABLED", "Enable the Gmail API"),
        ("ACCESS_TOKEN_SCOPE_INSUFFICIENT", "Gmail read-only permission was not granted"),
        ("unknown-policy", "Google denied the Gmail mailbox check"),
    ],
)
def test_profile_errors_show_safe_specific_messages(
    client, google_settings, monkeypatch, caplog, reason, expected
):
    _, query = begin(client)
    mock_oauth(monkeypatch, query)
    monkeypatch.setattr(
        google_oauth,
        "google_request",
        lambda *a, **k: httpx.Response(
            403,
            json={
                "error": {"message": "private-google-response", "details": [{"reason": reason}]},
            },
        ),
    )
    result = client.get(
        "/api/auth/google/callback",
        params={"state": query["state"][0], "code": "private-auth-code"},
        follow_redirects=False,
    )
    error = parse_qs(urlsplit(result.headers["location"]).query)["oauth_error"][0]
    assert expected in error
    assert "private-google-response" not in error
    assert "private-google-response" not in caplog.text
    assert "private-auth-code" not in caplog.text
    assert "google-access-secret" not in caplog.text
    assert "Gmail profile verification" in caplog.text


def test_missing_gmail_grant_reports_permission_without_claiming_other_causes(
    client, google_settings, monkeypatch
):
    _, query = begin(client)
    monkeypatch.setattr(
        google_oauth,
        "token_request",
        lambda _: {"access_token": "private", "scope": "openid email profile"},
    )
    result = client.get(
        "/api/auth/google/callback",
        params={"state": query["state"][0], "code": "code"},
        follow_redirects=False,
    )
    error = parse_qs(urlsplit(result.headers["location"]).query)["oauth_error"][0]
    assert error == "Please grant Gmail read-only access to connect Inbox Nudge"


def test_oauth_complete_one_time_pkce_exchange(client, google_settings, monkeypatch):
    verifier, query = begin(client)
    mock_oauth(monkeypatch, query)
    result = client.get(
        "/api/auth/google/callback",
        params={"state": query["state"][0], "code": "google-authorization-code"},
        follow_redirects=False,
    )
    assert "google-access-secret" not in result.headers["location"]
    code = parse_qs(urlsplit(result.headers["location"]).query)["oauth_code"][0]
    assert (
        client.post(
            "/api/auth/google/exchange", json={"code": code, "verifier": "wrong" * 16}
        ).status_code
        == 400
    )
    result = client.post("/api/auth/google/exchange", json={"code": code, "verifier": verifier})
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    auth = {"Authorization": f"Bearer {result.json()['access_token']}"}
    assert client.get("/api/me", headers=auth).json()["demo"] is False
    assert client.get("/api/me", headers=auth).json()["email"] == "new.user@gmail.com"
    assert (
        client.post(
            "/api/auth/google/exchange", json={"code": code, "verifier": verifier}
        ).status_code
        == 400
    )
    with SessionLocal() as db:
        account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.provider == "google"))
        assert "google-refresh-secret" not in account.refresh_token_encrypted
        assert "google-access-secret" not in account.access_token_encrypted


@pytest.mark.parametrize(
    "profile_email,refresh", [("wrong@gmail.com", True), ("new.user@gmail.com", False)]
)
def test_oauth_rejects_mismatched_mailbox_and_missing_offline_access(
    client, google_settings, monkeypatch, profile_email, refresh
):
    _, query = begin(client)
    mock_oauth(monkeypatch, query, profile_email=profile_email, refresh=refresh)
    result = client.get(
        "/api/auth/google/callback",
        params={"state": query["state"][0], "code": "code"},
        follow_redirects=False,
    )
    assert (
        "oauth_error=" in result.headers["location"]
        and "oauth_code=" not in result.headers["location"]
    )
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "new.user@gmail.com")) is None


@pytest.mark.parametrize(
    "claim,value",
    [
        ("nonce", "wrong"),
        ("email_verified", False),
        ("aud", "wrong-client"),
        ("iss", "https://evil.example"),
        ("exp", 1),
    ],
)
def test_google_id_token_validates_signature_audience_issuer_nonce_and_email(
    client, google_settings, monkeypatch, claim, value
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        google_oauth.keys,
        "get_signing_key_from_jwt",
        lambda _: SimpleNamespace(key=key.public_key()),
    )
    claims = {
        "sub": "subject",
        "iss": "https://accounts.google.com",
        "aud": google_settings.google_client_id,
        "email": "verified@gmail.com",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": now(),
        "exp": now() + timedelta(minutes=5),
    }
    encoded = jwt.encode(claims, key, algorithm="RS256")
    assert google_oauth.verify_identity(encoded, "expected-nonce")["sub"] == "subject"
    claims[claim] = value
    with pytest.raises(GoogleUnavailable):
        google_oauth.verify_identity(jwt.encode(claims, key, algorithm="RS256"), "expected-nonce")
