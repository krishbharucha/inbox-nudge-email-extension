import secrets
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from test_google import RFC_ID, SENDER_ID, imported

from app import dispatch, google_oauth
from app.db import SessionLocal
from app.extension_sessions import digest
from app.models import (
    ConnectedAccount,
    EmailThread,
    ExtensionInstallation,
    ExtensionSession,
    OAuthAttempt,
    PushSubscription,
    RecipientPreferences,
    User,
    now,
)

pytest_plugins = ["test_google"]


def exchange(
    client, google_settings, monkeypatch, *, user_id="sender", installation=None, web=False
):
    installation = installation or str(uuid4())
    monkeypatch.setattr(google_settings, "extension_id", "a" * 32)
    code, verifier = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    with SessionLocal() as db:
        db.add(
            OAuthAttempt(
                state_hash=secrets.token_hex(32),
                verifier_encrypted="already-consumed",
                nonce="verified-nonce",
                redirect_uri=google_settings.web_app_origin + "/"
                if web
                else f"https://{'a' * 32}.chromiumapp.org/google",
                app_challenge=google_oauth.challenge(verifier),
                status="authorized",
                expires_at=now() + timedelta(minutes=1),
                exchange_hash=google_oauth.digest(code),
                user_id=user_id,
            )
        )
        db.commit()
    response = client.post(
        "/api/auth/google/exchange",
        json={"code": code, "verifier": verifier, "installation_id": installation},
    )
    assert response.status_code == 200, response.text
    return response.json(), installation


def auth(tokens):
    return {"Authorization": "Bearer " + tokens["access_token"]}


def test_authenticated_metadata_and_auth_errors_are_not_cacheable(client, gmail_accounts):
    sender, _, _ = gmail_accounts
    for response in [client.get("/api/me", headers=sender), client.get("/api/me")]:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


def refresh_body(tokens, installation):
    return {"refresh_token": tokens["refresh_token"], "installation_id": installation}


def test_extension_exchange_issues_hashed_rotating_session_not_google_tokens(
    client, gmail_accounts, google_settings, monkeypatch
):
    tokens, installation = exchange(client, google_settings, monkeypatch)
    assert "refresh_token" in tokens and len(tokens["refresh_token"]) == 64
    assert client.get("/api/me", headers=auth(tokens)).status_code == 200
    with SessionLocal() as db:
        row = db.get(ExtensionSession, digest(tokens["refresh_token"]))
        assert row and row.installation_id == installation
        assert row.token_hash != tokens["refresh_token"]
        assert "google-access" not in str(tokens) and "google-refresh" not in str(tokens)
    web, _ = exchange(client, google_settings, monkeypatch, web=True)
    assert "refresh_token" not in web


def test_rotation_has_absolute_lifetime_and_replay_revokes_all_family_tokens(
    client, gmail_accounts, google_settings, monkeypatch
):
    tokens, installation = exchange(client, google_settings, monkeypatch)
    result = client.post("/api/auth/extension/refresh", json=refresh_body(tokens, installation))
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    rotated = result.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert rotated["refresh_expires_at"].replace("+00:00", "Z") == tokens[
        "refresh_expires_at"
    ].replace("+00:00", "Z")
    assert client.get("/api/me", headers=auth(rotated)).status_code == 200
    assert (
        client.post(
            "/api/auth/extension/refresh", json=refresh_body(tokens, installation)
        ).status_code
        == 401
    )
    assert client.get("/api/me", headers=auth(rotated)).status_code == 401
    assert (
        client.post(
            "/api/auth/extension/refresh", json=refresh_body(rotated, installation)
        ).status_code
        == 401
    )


@pytest.mark.parametrize("change", ["expired", "version", "disconnected", "wrong-device"])
def test_refresh_rejects_invalid_connections(
    client, gmail_accounts, google_settings, monkeypatch, change
):
    tokens, installation = exchange(client, google_settings, monkeypatch)
    with SessionLocal() as db:
        if change == "expired":
            db.get(ExtensionSession, digest(tokens["refresh_token"])).expires_at = (
                now() - timedelta(seconds=1)
            )
        if change == "version":
            db.get(User, "sender").session_version += 1
        if change == "disconnected":
            db.scalar(
                select(ConnectedAccount).where(ConnectedAccount.user_id == "sender")
            ).connection_status = "disconnected"
        db.commit()
    body = refresh_body(tokens, str(uuid4()) if change == "wrong-device" else installation)
    assert client.post("/api/auth/extension/refresh", json=body).status_code == 401
    assert client.get("/api/me", headers=auth(tokens)).status_code == 401


def test_logout_revokes_this_profile_and_preserves_other_profile(
    client, gmail_accounts, google_settings, monkeypatch
):
    one, id_one = exchange(client, google_settings, monkeypatch)
    two, id_two = exchange(client, google_settings, monkeypatch)
    for tokens, id_ in [(one, id_one), (two, id_two)]:
        assert (
            client.post(
                "/api/extension/installation",
                headers=auth(tokens),
                json={"installation_id": id_, "notifications_enabled": True},
            ).status_code
            == 200
        )
    assert (
        client.post("/api/auth/extension/logout", json=refresh_body(one, id_one)).status_code == 200
    )
    assert client.get("/api/me", headers=auth(one)).status_code == 401
    assert client.get("/api/me", headers=auth(two)).status_code == 200
    with SessionLocal() as db:
        assert db.get(ExtensionInstallation, id_one) is None
        assert db.get(ExtensionInstallation, id_two)


def test_logout_and_refresh_cannot_leave_an_active_successor(
    client, gmail_accounts, google_settings, monkeypatch
):
    tokens, installation = exchange(client, google_settings, monkeypatch)
    body = refresh_body(tokens, installation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = [
            pool.submit(client.post, "/api/auth/extension/" + route, json=body)
            for route in ["refresh", "logout"]
        ]
        responses = [value.result(timeout=10) for value in values]
    assert responses[1].status_code == 200
    assert responses[0].status_code in {200, 401}
    if responses[0].status_code == 200:
        assert client.get("/api/me", headers=auth(responses[0].json())).status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(ExtensionSession)) is None


def test_disconnecting_google_invalidates_refresh_sessions_and_devices(
    client, gmail_accounts, google_settings, monkeypatch
):
    tokens, installation = exchange(client, google_settings, monkeypatch)
    client.post(
        "/api/extension/installation",
        headers=auth(tokens),
        json={"installation_id": installation, "notifications_enabled": True},
    )
    assert client.post("/api/accounts/disconnect", headers=auth(tokens)).status_code == 200
    assert (
        client.post(
            "/api/auth/extension/refresh", json=refresh_body(tokens, installation)
        ).status_code
        == 401
    )
    with SessionLocal() as db:
        assert db.scalar(select(ExtensionInstallation)) is None
        assert db.scalar(select(ExtensionSession)) is None


def extension_recipient(client, recipient, google_settings, monkeypatch):
    monkeypatch.setattr(google_settings, "vapid_private_key", SecretStr(""))
    monkeypatch.setattr(google_settings, "vapid_public_key", "")
    with SessionLocal() as db:
        db.execute(delete(PushSubscription))
        db.commit()
    id_ = str(uuid4())
    response = client.post(
        "/api/extension/installation",
        headers=recipient,
        json={"installation_id": id_, "notifications_enabled": True},
    )
    assert response.status_code == 200, response.text
    return id_


def test_extension_recipient_needs_no_website_push_subscription(
    client, gmail_accounts, google_settings, monkeypatch
):
    sender, recipient, _ = gmail_accounts
    extension_recipient(client, recipient, google_settings, monkeypatch)
    id_ = imported(client, sender)
    plan = client.get(f"/api/threads/{id_}/eligibility", headers=sender).json()
    assert plan["status"] == "delivered" and plan["push_available"]
    value = client.post(
        "/api/reminders",
        headers=sender,
        json={"thread_id": id_, "idempotency_key": "extension-nudge-123"},
    ).json()
    assert value["status"] == "delivered" and value["delivery_channel"] == "in_app"
    # Device delivery is polled by Chrome, never Web Push or email.
    monkeypatch.setattr(
        dispatch, "webpush", lambda **kw: pytest.fail("Website push is unnecessary")
    )
    monkeypatch.setattr(dispatch, "send_email", lambda *a: pytest.fail("Email must not be used"))
    dispatch.dispatch_one(value["id"])
    assert (
        client.get(f"/api/reminders/{value['id']}", headers=sender).json()["notification_status"]
        == "extension_pending"
    )
    url = f"/api/extension/reminders/{value['id']}/notified"
    assert client.post(url, headers=sender).status_code == 404
    assert client.post(url, headers=recipient).status_code == 200
    assert client.post(url, headers=recipient).status_code == 200
    assert (
        client.get(f"/api/reminders/{value['id']}", headers=sender).json()["notification_status"]
        == "extension_notified"
    )


def test_installation_cannot_be_claimed_by_another_account(
    client, gmail_accounts, alex, google_settings, monkeypatch
):
    sender, recipient, _ = gmail_accounts
    id_ = extension_recipient(client, recipient, google_settings, monkeypatch)
    body = {"installation_id": id_, "notifications_enabled": True}
    assert client.post("/api/extension/installation", headers=sender, json=body).status_code == 409
    assert (
        client.post(
            "/api/extension/installation",
            headers=alex,
            json={**body, "installation_id": str(uuid4())},
        ).status_code
        == 403
    )
    assert client.post("/api/extension/installation", json=body).status_code == 401


def test_stale_extension_does_not_claim_notification_delivery(
    client, gmail_accounts, google_settings, monkeypatch
):
    sender, recipient, _ = gmail_accounts
    install = extension_recipient(client, recipient, google_settings, monkeypatch)
    id_ = imported(client, sender)
    with SessionLocal() as db:
        db.get(ExtensionInstallation, install).last_seen_at = now() - timedelta(days=8)
        db.commit()
    plan = client.get(f"/api/threads/{id_}/eligibility", headers=sender).json()
    assert plan["status"] == "email_permission_required" and not plan["push_available"]


def test_extension_receipt_respects_recipient_controls(
    client, gmail_accounts, google_settings, monkeypatch
):
    sender, recipient, _ = gmail_accounts
    extension_recipient(client, recipient, google_settings, monkeypatch)
    id_ = imported(client, sender)
    value = client.post(
        "/api/reminders",
        headers=sender,
        json={"thread_id": id_, "idempotency_key": "extension-controlled"},
    ).json()
    with SessionLocal() as db:
        db.get(RecipientPreferences, "recipient").notification_enabled = False
        db.commit()
    assert (
        client.post(
            f"/api/extension/reminders/{value['id']}/notified", headers=recipient
        ).status_code
        == 409
    )
    assert client.post(f"/api/extension/reminders/{value['id']}/notified").status_code == 401


def test_gmail_dom_fallback_uses_provider_verified_unique_conversation(client, gmail_accounts):
    sender, _, state = gmail_accounts
    response = client.post(
        "/api/threads/resolve",
        headers=sender,
        json={"subject": "Project check-in", "recipient_email": "recipient@gmail.com"},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        value = db.get(EmailThread, response.json()["id"])
        assert (
            value.provider_thread_id == SENDER_ID
            and value.original_message_id == RFC_ID
            and value.preview == ""
        )
    assert all(method == "GET" for method, _, _ in state["calls"])


@pytest.mark.parametrize("change", ["recipient", "subject", "ambiguous", "more-pages"])
def test_dom_fallback_rejects_incorrect_or_ambiguous_hints(
    client, gmail_accounts, monkeypatch, change
):
    from app.google_http import GmailClient

    sender, _, state = gmail_accounts
    original = GmailClient.get

    def get(self, path, params=None):
        if path == "/threads" and change in {"ambiguous", "more-pages"}:
            return {
                "threads": [{"id": SENDER_ID}, {"id": "dddddddddddddddd"}],
                **({"nextPageToken": "more"} if change == "more-pages" else {}),
            }
        if path.endswith("/dddddddddddddddd"):
            value = deepcopy(state["sender"])
            value["id"] = "dddddddddddddddd"
            return value
        return original(self, path, params)

    monkeypatch.setattr(GmailClient, "get", get)
    body = {
        "subject": "Unrelated" if change == "subject" else "Project check-in",
        "recipient_email": "other@example.com" if change == "recipient" else "recipient@gmail.com",
    }
    assert client.post("/api/threads/resolve", headers=sender, json=body).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(EmailThread).where(EmailThread.sender_user_id == "sender")) is None
