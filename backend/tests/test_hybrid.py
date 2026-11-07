import base64
import json
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException
from sqlalchemy import delete, select
from test_google import (
    RFC_ID,
    SENDER_ID,
    imported,
    message,
)

from app import dispatch, email_delivery
from app.db import SessionLocal
from app.google_http import GMAIL_SCOPE, GMAIL_SEND_SCOPE, decrypt
from app.models import (
    ConnectedAccount,
    EmailPreference,
    PushSubscription,
    RecipientPreferences,
    Reminder,
    now,
)

pytest_plugins = ["test_google"]


def fallback(*, send=True):
    with SessionLocal() as db:
        db.execute(delete(PushSubscription))
        if send:
            account = db.scalar(
                select(ConnectedAccount).where(ConnectedAccount.user_id == "sender")
            )
            account.granted_scopes = [*account.granted_scopes, GMAIL_SEND_SCOPE]
        db.commit()


def request_email(client, sender, thread_id, key="email-test-key-123"):
    return client.post(
        "/api/reminders",
        headers=sender,
        json={
            "thread_id": thread_id,
            "idempotency_key": key,
            "delivery_channel": "email",
        },
    )


def status(client, sender, reminder_id):
    return client.get(f"/api/reminders/{reminder_id}", headers=sender).json()


def test_email_requires_separate_grant_and_explicit_confirmation(client, gmail_accounts):
    sender, _, state = gmail_accounts
    thread_id = imported(client, sender)
    fallback(send=False)
    plan = client.get(f"/api/threads/{thread_id}/eligibility", headers=sender).json()
    assert plan["status"] == "email_permission_required"
    assert plan["delivery_channel"] == "email" and not plan["push_available"]
    assert "gentle reminder" in plan["email_preview"]
    assert (
        client.post(
            "/api/reminders",
            headers=sender,
            json={
                "thread_id": thread_id,
                "idempotency_key": "no-email-consent",
            },
        ).status_code
        == 409
    )
    result = request_email(client, sender, thread_id)
    assert result.status_code == 201 and result.json()["status"] == "email_permission_required"
    assert all(method == "GET" for method, _, _ in state["calls"])


def test_email_queued_sent_once_and_threaded_mime(client, gmail_accounts, monkeypatch):
    sender, _, state = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    calls = []

    def send(token, thread, raw):
        calls.append((token, thread, raw))
        return "email_sent", "aabbccddeeff0011"

    monkeypatch.setattr(dispatch, "send_email", send)
    result = request_email(client, sender, thread_id).json()
    assert result["status"] == "email_pending" and result["delivered_at"] is None
    assert not calls
    # Retrying the same HTTP request returns the same job, never another send.
    assert request_email(client, sender, thread_id).json()["id"] == result["id"]
    dispatch.dispatch_one(result["id"])
    dispatch.dispatch_one(result["id"])
    assert len(calls) == 1 and calls[0][:2] == ("access-sender", SENDER_ID)
    mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(calls[0][2]))
    assert mime["From"].addresses[0].addr_spec == "sender@gmail.com"
    assert mime["To"].addresses[0].addr_spec == "recipient@gmail.com"
    assert mime["Subject"] == "Project check-in"
    assert mime["In-Reply-To"] == RFC_ID and mime["References"] == RFC_ID
    assert mime["Message-ID"] == f"<nudge.{result['id']}@gmail.com>"
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "127.0.0.1:8000/api/email/preferences/" in mime["List-Unsubscribe"]
    assert "/api/email/preferences/" in mime.get_content()
    assert "This body snippet" not in mime.get_content()
    final = status(client, sender, result["id"])
    assert final["status"] == "email_sent" and final["delivered_at"]
    assert (
        request_email(client, sender, thread_id, "another-email-key").json()["status"]
        == "cooldown_active"
    )
    assert all(method == "GET" for method, _, _ in state["calls"])


@pytest.mark.parametrize("result_status", ["email_failed", "email_unknown"])
def test_failed_and_ambiguous_sends_are_not_automatically_retried(
    client, gmail_accounts, monkeypatch, result_status
):
    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    calls = []
    monkeypatch.setattr(
        dispatch, "send_email", lambda *args: (calls.append(args) or result_status, None)
    )
    value = request_email(client, sender, thread_id).json()
    dispatch.dispatch_one(value["id"])
    dispatch.drain()
    dispatch.dispatch_one(value["id"])
    assert len(calls) == 1 and status(client, sender, value["id"])["status"] == result_status
    if result_status == "email_unknown":
        assert (
            request_email(client, sender, thread_id, "no-duplicate-key").json()["status"]
            == "cooldown_active"
        )


@pytest.mark.parametrize(
    "change, expected",
    [
        ("reply", "thread_replied"),
        ("blocked", "recipient_disabled"),
        ("disabled", "recipient_disabled"),
        ("scope", "email_permission_required"),
    ],
)
def test_worker_rechecks_changes_before_sending(
    client, gmail_accounts, monkeypatch, change, expected
):
    sender, _, state = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    value = request_email(client, sender, thread_id).json()
    calls = []
    monkeypatch.setattr(dispatch, "send_email", lambda *args: calls.append(args))
    if change == "reply":
        state["sender"]["messages"].append(message(reply=True))
    else:
        with SessionLocal() as db:
            if change == "blocked":
                db.get(RecipientPreferences, "recipient").blocked_senders = ["sender@gmail.com"]
            elif change == "disabled":
                db.get(RecipientPreferences, "recipient").reminders_enabled = False
            else:
                db.scalar(
                    select(ConnectedAccount).where(ConnectedAccount.user_id == "sender")
                ).granted_scopes = [GMAIL_SCOPE]
            db.commit()
    dispatch.dispatch_one(value["id"])
    assert not calls
    assert status(client, sender, value["id"])["status"] == expected


def test_sender_cannot_select_email_to_bypass_connected_recipient(client, gmail_accounts):
    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    assert request_email(client, sender, thread_id).status_code == 409


def test_unsubscribe_is_signup_free_get_safe_and_blocks_delivery(
    client, gmail_accounts, monkeypatch
):
    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    # Create a real opaque capability as MIME preparation would.
    with SessionLocal() as db:
        from app.models import User

        pref = email_delivery.email_preference(db, db.get(User, "sender"), "recipient@gmail.com")
        token = decrypt(pref.token_encrypted)
        assert token not in pref.token_hash and token not in pref.token_encrypted
        db.commit()
    url = f"/api/email/preferences/{token}"
    assert client.get(url).json() == {"sender_name": "Sender", "blocked": False}
    assert client.get(url + "/unsubscribe", follow_redirects=False).status_code == 200
    assert not client.get(url).json()["blocked"]
    value = request_email(client, sender, thread_id).json()
    assert client.post(url + "/unsubscribe").json()["blocked"]
    assert client.post(url + "/unsubscribe").status_code == 200
    calls = []
    monkeypatch.setattr(dispatch, "send_email", lambda *args: calls.append(args))
    dispatch.dispatch_one(value["id"])
    assert not calls and status(client, sender, value["id"])["status"] == "recipient_disabled"
    assert client.get("/api/email/preferences/forged-token").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(EmailPreference)).blocked_at


@pytest.mark.parametrize(
    "code, expected",
    [
        (200, "email_sent"),
        (400, "email_failed"),
        (401, "email_failed"),
        (403, "email_failed"),
        (429, "email_failed"),
        (500, "email_unknown"),
        (503, "email_unknown"),
        (408, "email_unknown"),
    ],
)
def test_google_send_response_semantics(monkeypatch, code, expected):
    calls = []

    def mock(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(code, json={"id": "aabbccddeeff"})

    monkeypatch.setattr(email_delivery, "google_request", mock)
    assert email_delivery.send_email("test-token", SENDER_ID, "mime")[0] == expected
    assert len(calls) == 1 and calls[0][0] == "POST" and calls[0][1].endswith("/messages/send")
    assert calls[0][2]["json"] == {"threadId": SENDER_ID, "raw": "mime"}


@pytest.mark.parametrize("body", [{}, [], {"id": None}, {"id": ""}])
def test_malformed_success_is_ambiguous(monkeypatch, body):
    monkeypatch.setattr(
        email_delivery, "google_request", lambda *a, **k: httpx.Response(200, json=body)
    )
    assert email_delivery.send_email("test", SENDER_ID, "mime")[0] == "email_unknown"


def test_timeout_is_ambiguous(monkeypatch):
    def timeout(*a, **k):
        raise httpx.ReadTimeout("mock timeout")

    monkeypatch.setattr(email_delivery, "google_request", timeout)
    assert email_delivery.send_email("test", SENDER_ID, "mime")[0] == "email_unknown"


def test_competing_workers_submit_email_once(client, gmail_accounts, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    value = request_email(client, sender, thread_id).json()
    calls = []
    monkeypatch.setattr(
        dispatch,
        "send_email",
        lambda *args: (calls.append(args) or "email_sent", "aabbccddeeff0011"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch.dispatch_one, value["id"]) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert len(calls) == 1 and status(client, sender, value["id"])["status"] == "email_sent"


def test_background_delivery_runs_after_confirmed_api_request(
    client, gmail_accounts, google_settings, monkeypatch
):
    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    monkeypatch.setattr(google_settings, "delivery_worker_enabled", True)
    calls = []
    monkeypatch.setattr(
        dispatch,
        "send_email",
        lambda *args: (calls.append(args) or "email_sent", "aabbccddeeff0011"),
    )
    result = request_email(client, sender, thread_id)
    assert result.status_code == 201
    assert result.json()["status"] == "email_pending"
    assert len(calls) == 1
    assert status(client, sender, result.json()["id"])["status"] == "email_sent"


def test_email_recipient_does_not_need_an_account(client, gmail_accounts, monkeypatch):
    sender, _, state = gmail_accounts
    from app.models import User

    # A new Gmail address exists only in verified provider metadata.
    state["sender"]["messages"][0]["payload"]["headers"][1]["value"] = "outside@example.com"
    thread_id = imported(client, sender)
    fallback()
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "outside@example.com")) is None
    calls = []
    monkeypatch.setattr(
        dispatch,
        "send_email",
        lambda *args: (calls.append(args) or "email_sent", "aabbccddeeff0011"),
    )
    value = request_email(client, sender, thread_id).json()
    dispatch.dispatch_one(value["id"])
    assert len(calls) == 1
    mime = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(calls[0][2]))
    assert str(mime["To"]) == "outside@example.com"
    assert status(client, sender, value["id"])["status"] == "email_sent"


@pytest.mark.parametrize("found", [False, True])
def test_reconcile_leaves_unconfirmed_mail_unknown(client, gmail_accounts, monkeypatch, found):
    from app.google_http import GmailClient

    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    value = request_email(client, sender, thread_id).json()
    with SessionLocal() as db:
        row = db.get(Reminder, value["id"])
        row.status, row.outbound_message_id = "email_unknown", "<nudge.test@gmail.com>"
        db.commit()

    def get(self, path, params=None):
        if path == "/messages":
            return {"messages": [{"id": "aabbccddeeff0011"}] if found else []}
        return {
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<nudge.test@gmail.com>"},
                    {"name": "From", "value": "other@gmail.com"},
                    {"name": "To", "value": "recipient@gmail.com"},
                ]
            },
        }

    monkeypatch.setattr(GmailClient, "get", get)
    assert (
        client.post(f"/api/reminders/{value['id']}/reconcile", headers=sender).json()["status"]
        == "email_unknown"
    )


def test_vapid_pair_validation(google_settings):
    from pydantic import SecretStr, ValidationError

    from app.config import Settings

    key = ec.generate_private_key(ec.SECP256R1())
    private = base64.urlsafe_b64encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    public = (
        base64.urlsafe_b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        )
        .rstrip(b"=")
        .decode()
    )
    assert Settings(vapid_private_key=SecretStr(private), vapid_public_key=public).push_configured
    for bad in ["", "not-a-public-key"]:
        with pytest.raises(ValidationError):
            Settings(vapid_private_key=SecretStr(private), vapid_public_key=bad)


def test_crash_boundary_becomes_unknown_never_resends(client, gmail_accounts, monkeypatch):
    sender, _, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    value = request_email(client, sender, thread_id).json()
    with SessionLocal() as db:
        row = db.get(Reminder, value["id"])
        row.status, row.attempted_at = "email_sending", now() - timedelta(minutes=6)
        db.commit()
    calls = []
    monkeypatch.setattr(dispatch, "send_email", lambda *args: calls.append(args))
    dispatch.drain()
    assert not calls and status(client, sender, value["id"])["status"] == "email_unknown"


def subscription(endpoint="https://fcm.googleapis.com/fcm/send/mock-test-endpoint"):
    key = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    return {
        "endpoint": endpoint,
        "keys": {
            "p256dh": base64.urlsafe_b64encode(key).rstrip(b"=").decode(),
            "auth": base64.urlsafe_b64encode(b"x" * 16).rstrip(b"=").decode(),
        },
    }


def test_push_subscription_is_private_account_scoped_and_removable(client, gmail_accounts):
    sender, recipient, _ = gmail_accounts
    value = subscription()
    result = client.post("/api/notifications/subscriptions", headers=recipient, json=value)
    assert result.status_code == 200
    assert (
        client.post("/api/notifications/subscriptions", headers=sender, json=value).status_code
        == 409
    )
    with SessionLocal() as db:
        row = db.get(PushSubscription, result.json()["id"])
        assert value["endpoint"] not in row.subscription_encrypted
        assert json.loads(decrypt(row.subscription_encrypted))["endpoint"] == value["endpoint"]
    client.post(
        "/api/notifications/subscriptions/remove",
        headers=sender,
        json={"endpoint": value["endpoint"]},
    )
    assert client.get("/api/notifications/config", headers=recipient).json()["subscriptions"] == 2
    client.post("/api/notifications/subscriptions/remove-all", headers=recipient)
    assert client.get("/api/notifications/config", headers=recipient).json()["subscriptions"] == 0


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/send/a",
        "https://127.0.0.1/a",
        "https://fcm.googleapis.com.attacker.test/a",
        "https://user:pass@fcm.googleapis.com/a",
        "https://fcm.googleapis.com:8000/a",
        "https://example.com/a",
    ],
)
def test_push_rejects_untrusted_endpoints(client, gmail_accounts, endpoint):
    _, recipient, _ = gmail_accounts
    assert (
        client.post(
            "/api/notifications/subscriptions", headers=recipient, json=subscription(endpoint)
        ).status_code
        == 422
    )


def test_push_rejects_invalid_encryption_key(client, gmail_accounts):
    _, recipient, _ = gmail_accounts
    value = subscription()
    value["keys"]["p256dh"] = base64.urlsafe_b64encode(b"x" * 65).decode()
    assert (
        client.post("/api/notifications/subscriptions", headers=recipient, json=value).status_code
        == 422
    )


@pytest.mark.parametrize(
    "outcome, expected",
    [("accepted", "push_accepted"), ("expired", "failed"), ("disabled", "suppressed")],
)
def test_push_reports_provider_acceptance_and_respects_preferences(
    client, gmail_accounts, monkeypatch, outcome, expected
):
    sender, recipient, _ = gmail_accounts
    thread_id = imported(client, sender)
    result = client.post(
        "/api/reminders",
        headers=sender,
        json={"thread_id": thread_id, "idempotency_key": "push-reminder-key"},
    ).json()
    assert result["status"] == "delivered" and result["notification_status"] == "queued"
    calls = []

    def mock(**kwargs):
        calls.append(kwargs)
        if outcome == "expired":
            raise WebPushException("expired", response=SimpleNamespace(status_code=410))
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr(dispatch, "webpush", mock)
    if outcome == "disabled":
        with SessionLocal() as db:
            db.get(RecipientPreferences, "recipient").notification_enabled = False
            db.commit()
    dispatch.dispatch_one(result["id"])
    dispatch.dispatch_one(result["id"])
    assert len(calls) == (0 if outcome == "disabled" else 1)
    assert status(client, sender, result["id"])["notification_status"] == expected
    if calls:
        payload = json.loads(calls[0]["data"])
        assert payload["url"] == "/?view=priority" and calls[0]["timeout"] == 10
    if outcome == "expired":
        assert (
            client.get("/api/notifications/config", headers=recipient).json()["subscriptions"] == 0
        )


def test_reconcile_checks_sent_without_resending(client, gmail_accounts, monkeypatch):
    sender, recipient, _ = gmail_accounts
    thread_id = imported(client, sender)
    fallback()
    value = request_email(client, sender, thread_id).json()
    with SessionLocal() as db:
        row = db.get(Reminder, value["id"])
        row.status = "email_unknown"
        row.outbound_message_id = f"<nudge.{row.id}@gmail.com>"
        expected_id = row.outbound_message_id
        db.commit()
    from app.google_http import GmailClient

    calls = []

    def get(self, path, params=None):
        calls.append((path, params))
        if path == "/messages":
            assert params["labelIds"] == "SENT" and expected_id in params["q"]
            return {"messages": [{"id": "aabbccddeeff0011"}]}
        return {
            "labelIds": ["SENT"],
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": expected_id},
                    {"name": "From", "value": "sender@gmail.com"},
                    {"name": "To", "value": "recipient@gmail.com"},
                ]
            },
        }

    monkeypatch.setattr(GmailClient, "get", get)
    url = f"/api/reminders/{value['id']}/reconcile"
    assert client.post(url, headers=recipient).status_code == 403
    assert client.post(url, headers=sender).json()["status"] == "email_sent"
    assert len(calls) == 2


def test_incremental_send_scope_is_opt_in(client, google_settings):
    from app.google_oauth import challenge

    verifier = "a" * 64
    params = {"redirect_uri": "http://127.0.0.1:5173/", "code_challenge": challenge(verifier)}
    result = client.get("/api/auth/google/start", params=params, follow_redirects=False)
    from urllib.parse import parse_qs, urlsplit

    assert result.status_code in {302, 307}, result.text
    assert GMAIL_SEND_SCOPE not in parse_qs(urlsplit(result.headers["location"]).query)["scope"][0]
    params["email_reminders"] = "true"
    result = client.get("/api/auth/google/start", params=params, follow_redirects=False)
    assert GMAIL_SEND_SCOPE in parse_qs(urlsplit(result.headers["location"]).query)["scope"][0]
