import os
import tempfile

_directory = tempfile.TemporaryDirectory(prefix="nudge-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_directory.name}/test.db"
os.environ["JWT_SECRET"] = "tests-only-secret-that-is-at-least-32-characters"
os.environ["DEMO_MODE"] = "true"
os.environ["APP_ENV"] = "development"
os.environ["WEB_AUTH_ENABLED"] = "true"
os.environ["REQUEST_LIMITS_ENABLED"] = "false"
os.environ["DELIVERY_WORKER_ENABLED"] = "false"
os.environ["VAPID_PRIVATE_KEY"] = ""
os.environ["VAPID_PUBLIC_KEY"] = ""
os.environ["GOOGLE_CLIENT_ID"] = ""
os.environ["GOOGLE_CLIENT_SECRET"] = ""
os.environ["TOKEN_ENCRYPTION_KEY"] = ""
os.environ["EXTENSION_ID"] = ""
os.environ["WEB_APP_ORIGIN"] = "http://127.0.0.1:5173"
os.environ["GOOGLE_REDIRECT_URI"] = "http://127.0.0.1:8000/api/auth/google/callback"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    with TestClient(app) as value:
        yield value


@pytest.fixture
def alex(client):
    result = client.post("/api/auth/demo", json={"persona": "alex"}).json()
    return {"Authorization": f"Bearer {result['access_token']}"}


@pytest.fixture
def maya(client):
    result = client.post("/api/auth/demo", json={"persona": "maya"}).json()
    return {"Authorization": f"Bearer {result['access_token']}"}
