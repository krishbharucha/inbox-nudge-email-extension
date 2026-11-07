from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, ConnectedAccount, EmailThread, Reminder, now


def send(client, headers, thread="launch", key="unique-request-0001"):
    return client.post(
        "/api/reminders", headers=headers, json={"thread_id": thread, "idempotency_key": key}
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/me",
        "/api/threads",
        "/api/reminders",
        "/api/inbox/priority",
        "/api/preferences",
        "/api/reminders/welcome-0",
        "/api/threads/launch/eligibility",
    ],
)
def test_authentication_required(client, path):
    assert client.get(path).status_code == 401


def test_write_authentication_required(client):
    assert send(client, {}).status_code == 401
    assert client.post("/api/reminders/welcome-0/dismiss").status_code == 401
    assert client.put("/api/preferences", json={}).status_code == 401


def test_deliver_to_recipient_account_scoped_inbox(client, alex, maya):
    result = send(client, alex)
    assert result.status_code == 201
    body = result.json()
    assert body["status"] == "delivered" and body["simulated"]
    assert body["provider_thread_id"] == "alex-mailbox-launch"
    inbox = client.get("/api/inbox/priority", headers=maya).json()
    assert inbox[0]["id"] == body["id"]
    assert inbox[0]["provider_thread_id"] == "maya-mailbox-launch"
    assert len(client.get("/api/reminders", headers=alex).json()) == 4
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.resource_id == body["id"]))


def test_cooldown_and_idempotency(client, alex):
    first = send(client, alex).json()
    assert send(client, alex).json()["id"] == first["id"]
    second = send(client, alex, key="different-request").json()
    assert second["status"] == "cooldown_active"
    assert second["delivered_at"] is None
    assert send(client, alex, thread="recruiter").status_code == 409


def test_concurrent_requests_preserve_cooldown(client, alex):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda i: send(client, alex, key=f"parallel-request-{i}"), range(4))
        )
    assert all(result.status_code == 201 for result in results)
    statuses = [result.json()["status"] for result in results]
    assert statuses.count("delivered") == 1
    assert statuses.count("cooldown_active") == 3


def test_concurrent_retries_return_same_record(client, alex):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: send(client, alex), range(4)))
    assert all(result.status_code == 201 for result in results)
    assert len({result.json()["id"] for result in results}) == 1


def test_unconnected_recorded_without_claiming_delivery(client, alex):
    result = send(client, alex, thread="recruiter").json()
    assert result["status"] == "recipient_not_connected"
    assert result["delivered_at"] is None
    with SessionLocal() as db:
        assert db.get(Reminder, result["id"]).recipient_user_id is None


def test_replied_and_unverified_fail_closed(client, alex):
    assert send(client, alex, thread="replied").json()["status"] == "thread_replied"
    with SessionLocal() as db:
        db.get(EmailThread, "launch").verification_source = "dom"
        db.commit()
    result = send(client, alex, key="unverified-request").json()
    assert result["status"] == "unable_to_verify"
    assert result["delivered_at"] is None


def test_ownership_and_dismissal(client, alex, maya):
    assert send(client, maya).status_code == 404
    assert client.get("/api/threads/launch/eligibility", headers=maya).status_code == 404
    assert client.get("/api/reminders/welcome-1", headers=maya).status_code == 404
    value = send(client, alex).json()
    url = f"/api/reminders/{value['id']}/dismiss"
    assert client.post(url, headers=alex).status_code == 403
    dismissed = client.post(url, headers=maya).json()
    assert dismissed["status"] == "dismissed" and dismissed["dismissed_at"]
    assert client.post(url, headers=maya).json()["status"] == "dismissed"
    assert client.get("/api/inbox/priority", headers=maya).json() == []


@pytest.mark.parametrize("enabled,blocked", [(False, []), (True, ["alex.morgan@example.com"])])
def test_recipient_preferences_enforced(client, alex, maya, enabled, blocked):
    result = client.put(
        "/api/preferences",
        headers=maya,
        json={
            "reminders_enabled": enabled,
            "notification_enabled": True,
            "blocked_senders": blocked,
        },
    )
    assert result.status_code == 200
    assert send(client, alex).json()["status"] == "recipient_disabled"
    assert client.get("/api/inbox/priority", headers=maya).json() == []


def test_existing_reminders_hidden_when_sender_blocked(client, alex):
    client.put(
        "/api/preferences",
        headers=alex,
        json={
            "reminders_enabled": True,
            "notification_enabled": False,
            "blocked_senders": ["MAYA.CHEN@example.com"],
        },
    )
    result = client.get("/api/inbox/priority", headers=alex).json()
    assert len(result) == 2 and all(r["sender_name"] != "Maya Chen" for r in result)
    assert client.get("/api/preferences", headers=alex).json()["blocked_senders"] == [
        "maya.chen@example.com"
    ]


def test_preferences_read_write_roundtrip(client, alex):
    value = client.get("/api/preferences", headers=alex).json()
    assert set(value) == {"reminders_enabled", "notification_enabled", "blocked_senders"}
    value["reminders_enabled"] = False
    assert client.put("/api/preferences", headers=alex, json=value).status_code == 200
    assert client.get("/api/preferences", headers=alex).json() == value


def test_disconnected_recipient(client, alex):
    with SessionLocal() as db:
        account = db.scalar(select(ConnectedAccount).where(ConnectedAccount.user_id == "maya"))
        account.connection_status = "disconnected"
        db.commit()
    assert send(client, alex).json()["status"] == "recipient_not_connected"


def test_rolling_daily_limit(client, alex):
    for i in range(10):
        assert send(client, alex, thread="recruiter", key=f"request-budget-{i}").status_code == 201
    assert send(client, alex, key="request-over-budget").status_code == 429
    assert send(client, alex, thread="replied", key="replied-over-budget").status_code == 429
    with SessionLocal() as db:
        db.get(EmailThread, "launch").verification_source = "unverified"
        db.commit()
    assert send(client, alex, key="unverified-over-budget").status_code == 429
    # Retrying an existing request is safe even when the allowance is exhausted.
    assert send(client, alex, thread="recruiter", key="request-budget-0").status_code == 201
    assert client.get("/api/me", headers=alex).json()["daily_used"] == 10


def test_expired_and_forged_tokens_rejected(client):
    settings = get_settings()
    claims = {
        "sub": "alex",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now() - timedelta(hours=2),
        "exp": now() - timedelta(hours=1),
    }
    for token in [
        jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256"),
        jwt.encode(
            {**claims, "exp": now() + timedelta(hours=1)}, "wrong-secret" * 4, algorithm="HS256"
        ),
    ]:
        assert (
            client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
        )


def test_demo_disabled_in_production(client, alex, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert client.post("/api/auth/demo", json={"persona": "alex"}).status_code == 404
    assert client.get("/api/me", headers=alex).status_code == 401


def test_payload_validation_and_pagination(client, alex):
    assert send(client, alex, key="x").status_code == 422
    assert (
        client.post(
            "/api/reminders",
            headers=alex,
            json={
                "thread_id": "launch",
                "idempotency_key": "sufficient-length",
                "sender_user_id": "maya",
            },
        ).status_code
        == 422
    )
    assert client.get("/api/reminders?limit=101", headers=alex).status_code == 422
    assert client.get("/api/reminders?offset=-1", headers=alex).status_code == 422
    assert len(client.get("/api/reminders?limit=1", headers=alex).json()) == 1


def test_duplicate_threads_deduplicated_in_priority(client, alex, maya):
    first = send(client, alex).json()
    with SessionLocal() as db:
        reminder = db.get(Reminder, first["id"])
        reminder.delivered_at = now() - timedelta(hours=25)
        db.commit()
    assert send(client, alex, key="after-cooldown-request").json()["status"] == "delivered"
    assert len(client.get("/api/inbox/priority", headers=maya).json()) == 1
