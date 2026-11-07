"""Bound request sizes and provider traffic before parsing bodies or calling Google."""

import asyncio
import hashlib
import hmac
import time
from threading import Lock

from sqlalchemy import case, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.responses import JSONResponse

from .db import SessionLocal
from .models import RequestBucket


class RequestLimiter:
    def __init__(self, secret, session_factory=SessionLocal, clock=time.time):
        self.secret, self.session_factory, self.clock = secret.encode(), session_factory, clock
        self.next_cleanup = 0
        self.cleanup_lock = Lock()

    def consume(self, client, category, limit):
        timestamp = self.clock()
        window = int(timestamp // 60)
        key = hmac.new(self.secret, (category + ":" + client).encode(), hashlib.sha256).hexdigest()
        with self.session_factory() as db:
            insert = sqlite_insert if db.get_bind().dialect.name == "sqlite" else pg_insert
            statement = insert(RequestBucket).values(key=key, window_id=window, count=1)
            statement = statement.on_conflict_do_update(
                index_elements=[RequestBucket.key],
                set_={
                    "window_id": window,
                    "count": case(
                        (RequestBucket.window_id != window, 1),
                        (RequestBucket.count >= limit, limit + 1),
                        else_=RequestBucket.count + 1,
                    ),
                },
            ).returning(RequestBucket.count)
            count = db.scalar(statement)
            with self.cleanup_lock:
                if timestamp >= self.next_cleanup:
                    db.execute(delete(RequestBucket).where(RequestBucket.window_id < window - 2))
                    self.next_cleanup = timestamp + 30
            db.commit()
        return count <= limit, 60 - int(timestamp) % 60


class RequestSecurityMiddleware:
    def __init__(self, app, settings, limiter=None, max_body=128 * 1024):
        self.app, self.settings, self.max_body = app, settings, max_body
        self.limiter = limiter or RequestLimiter(settings.jwt_secret.get_secret_value())

    async def reject(self, scope, receive, send, status, message, retry=None):
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if retry is not None:
            headers["Retry-After"] = str(retry)
        await JSONResponse({"detail": message}, status_code=status, headers=headers)(
            scope, receive, send
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        if len(scope.get("raw_path", b"")) + len(scope.get("query_string", b"")) > 8192:
            return await self.reject(scope, receive, send, 414, "Request URL is too long")
        if self.settings.request_limits_enabled and scope["method"] != "OPTIONS":
            path = scope["path"]
            category, limit = (
                ("auth", 30)
                if path.startswith("/api/auth/")
                else (
                    ("verify", 45)
                    if path.startswith("/api/threads/")
                    or path == "/api/inbox/gmail"
                    or path.endswith("/reconcile")
                    else ("api", 1200)
                )
            )
            # Only the ASGI peer is trusted. Raw X-Forwarded-For is never used.
            # Configure Uvicorn's trusted proxies at the actual hosting boundary.
            client = (scope.get("client") or ("unknown", 0))[0]
            try:
                allowed, retry = await asyncio.to_thread(
                    self.limiter.consume, client, category, limit
                )
            except Exception:
                return await self.reject(
                    scope, receive, send, 503, "Request protection is temporarily unavailable"
                )
            if not allowed:
                return await self.reject(
                    scope, receive, send, 429, "Too many requests. Try again shortly.", retry
                )
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            headers = dict(scope.get("headers", []))
            try:
                declared = int(headers.get(b"content-length", b"0"))
            except ValueError:
                return await self.reject(scope, receive, send, 400, "Invalid request length")
            if declared < 0 or declared > self.max_body:
                return await self.reject(scope, receive, send, 413, "Request body is too large")
            chunks, length = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                length += len(chunk)
                if length > self.max_body:
                    return await self.reject(scope, receive, send, 413, "Request body is too large")
                chunks.append(chunk)
                if not message.get("more_body"):
                    break
            body, replayed = b"".join(chunks), False
            original_receive = receive

            async def replay():
                nonlocal replayed
                if replayed:
                    return await original_receive()
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}

            receive = replay
        await self.app(scope, receive, send)
