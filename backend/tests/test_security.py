import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from requests import Response as PushResponse
from requests.adapters import BaseAdapter
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import RequestBucket
from app.push import PushHTTPSession
from app.request_security import RequestLimiter, RequestSecurityMiddleware


def production_settings(**override):
    values = dict(
        app_env="production",
        demo_mode=False,
        jwt_secret="unique-generated-value-" * 4,
        database_url="postgresql+psycopg://user:private-password@db.inboxnudge.dev/app?sslmode=verify-full",
        google_client_id="client.apps.googleusercontent.com",
        google_client_secret="private-google-secret",
        token_encryption_key=Fernet.generate_key().decode(),
        extension_id="a" * 32,
        google_redirect_uri="https://api.inboxnudge.dev/api/auth/google/callback",
        web_auth_enabled=False,
        request_limits_enabled=True,
        cors_origins="",
    )
    return Settings(_env_file=None, **(values | override))


def test_production_configuration_masks_secrets_and_accepts_safe_values():
    settings = production_settings()
    assert settings.app_env == "production"
    for private in ["private-password", "private-google-secret", "unique-generated-value-"]:
        assert private not in repr(settings)
        assert private not in settings.model_dump_json()


@pytest.mark.parametrize(
    "override",
    [
        {"demo_mode": True},
        {"jwt_secret": "x" * 32},
        {"request_limits_enabled": False},
        {"extension_id": ""},
        {"google_client_secret": ""},
        {"database_url": "sqlite:///unsafe.db"},
        {"database_url": "postgresql+psycopg://user:pass@db.example/app"},
        {"google_redirect_uri": "http://127.0.0.1:8000/api/auth/google/callback"},
        {"google_redirect_uri": "https://api.example.com/api/auth/google/callback"},
        {"web_auth_enabled": True, "web_app_origin": "http://localhost:5173"},
        {"cors_origins": "*"},
    ],
)
def test_production_configuration_rejects_development_defaults(override):
    with pytest.raises(ValidationError):
        production_settings(**override)


def test_validation_errors_do_not_echo_credentials(client):
    secret = "private-refresh-value-that-must-not-be-returned"
    response = client.post(
        "/api/auth/extension/refresh", json={"refresh_token": secret, "installation_id": "invalid"}
    )
    assert response.status_code == 422
    assert secret not in response.text and "input" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_request_counters_are_shared_atomic_hashed_and_reset(client):
    timestamp = [6000.0]
    one = RequestLimiter("secret", clock=lambda: timestamp[0])
    two = RequestLimiter("secret", clock=lambda: timestamp[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(
            pool.map(
                lambda index: (one if index % 2 else two).consume("192.0.2.10", "auth", 3)[0],
                range(10),
            )
        )
    assert sum(values) == 3
    with SessionLocal() as db:
        row = db.scalar(select(RequestBucket))
        assert row.count == 4 and "192.0.2.10" not in row.key and len(row.key) == 64
    timestamp[0] += 60
    assert two.consume("192.0.2.10", "auth", 3)[0]


def test_forged_forwarded_addresses_cannot_bypass_auth_limits(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "request_limits_enabled", True)
    body = {"refresh_token": "x" * 64, "installation_id": str(uuid4())}
    for index in range(30):
        response = client.post(
            "/api/auth/extension/refresh",
            json=body,
            headers={"X-Forwarded-For": f"192.0.2.{index}"},
        )
        assert response.status_code == 401
    denied = client.post(
        "/api/auth/extension/refresh", json=body, headers={"X-Forwarded-For": "203.0.113.1"}
    )
    assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0


def guard_request(chunks, *, length=None, max_body=10):
    async def run():
        reached, output = [], []
        messages = iter(
            [
                {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
                for index, chunk in enumerate(chunks)
            ]
        )

        async def receive():
            return next(messages)

        async def send(message):
            output.append(message)

        async def app(scope, receive, send):
            reached.append(await receive())

        middleware = RequestSecurityMiddleware(
            app,
            SimpleNamespace(request_limits_enabled=False, jwt_secret=get_settings().jwt_secret),
            max_body=max_body,
        )
        scope = {
            "type": "http",
            "path": "/api/auth/extension/refresh",
            "method": "POST",
            "headers": [] if length is None else [(b"content-length", str(length).encode())],
        }
        await middleware(scope, receive, send)
        return reached, output

    return asyncio.run(run())


@pytest.mark.parametrize("length", [None, 1, 100])
def test_large_declared_chunked_or_misdeclared_bodies_do_not_reach_the_app(length):
    reached, output = guard_request([b"x" * 8, b"y" * 8], length=length)
    assert reached == [] and output[0]["status"] == 413


def test_normal_chunked_body_is_replayed_without_altering_it():
    reached, output = guard_request([b'{"x":', b"1}"])
    assert (
        reached == [{"type": "http.request", "body": b'{"x":1}', "more_body": False}] and not output
    )


def test_push_redirects_are_not_followed():
    class RedirectAdapter(BaseAdapter):
        def __init__(self):
            self.targets = []

        def send(self, request, **kwargs):
            self.targets.append(request.url)
            response = PushResponse()
            response.status_code = 307
            response.headers["Location"] = "http://127.0.0.1/private"
            response.request, response._content = request, b""
            return response

        def close(self):
            pass

    adapter = RedirectAdapter()
    with PushHTTPSession() as http:
        http.mount("https://", adapter)
        http.mount("http://", adapter)
        response = http.post("https://fcm.googleapis.com/push", data=b"encrypted")
    assert response.status_code == 307 and adapter.targets == ["https://fcm.googleapis.com/push"]
