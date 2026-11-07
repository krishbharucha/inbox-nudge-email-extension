"""Apply migrations, including adoption of the original unversioned demo schema."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .db import engine

LEGACY = {
    "users": {"id", "email", "display_name", "is_demo", "created_at"},
    "connected_accounts": {
        "id",
        "user_id",
        "provider",
        "provider_account_id",
        "connection_status",
        "last_seen_at",
    },
    "email_threads": {
        "id",
        "provider_thread_id",
        "recipient_provider_thread_id",
        "sender_user_id",
        "recipient_email",
        "subject",
        "preview",
        "last_message_at",
        "has_reply",
        "verification_source",
    },
    "reminders": {
        "id",
        "thread_id",
        "sender_user_id",
        "recipient_user_id",
        "idempotency_key",
        "status",
        "created_at",
        "delivered_at",
        "dismissed_at",
    },
    "recipient_preferences": {
        "user_id",
        "reminders_enabled",
        "blocked_senders",
        "notification_enabled",
    },
    "audit_logs": {"id", "user_id", "action", "resource_id", "details", "created_at"},
}


def main():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if tables and "alembic_version" not in tables:
        if tables != set(LEGACY) or any(
            {col["name"] for col in inspector.get_columns(table)} != columns
            for table, columns in LEGACY.items()
        ):
            raise RuntimeError(
                "Unversioned database does not match the original demo. Back it up and review its schema before migrating."
            )
        if engine.dialect.name == "sqlite" and engine.url.database:
            path = Path(engine.url.database)
            backup = path.with_name(
                path.name + ".backup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            )
            with sqlite3.connect(path) as source, sqlite3.connect(backup) as destination:
                source.backup(destination)
            backup.chmod(0o600)
        command.stamp(config, "0001")
    command.upgrade(config, "head")


if __name__ == "__main__":
    main()
