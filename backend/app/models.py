from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConnectedAccount(Base):
    __tablename__ = "connected_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(30), default="demo")
    provider_account_id: Mapped[str] = mapped_column(String(320))
    connection_status: Mapped[str] = mapped_column(String(30), default="connected")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_scopes: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")


class EmailThread(Base):
    __tablename__ = "email_threads"
    __table_args__ = (UniqueConstraint("sender_user_id", "provider_thread_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    # Gmail identifiers belong to a mailbox; never reuse the sender ID for the recipient.
    provider_thread_id: Mapped[str] = mapped_column(String(200))
    recipient_provider_thread_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500))
    preview: Mapped[str] = mapped_column(String(2000), default="")
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    has_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_source: Mapped[str] = mapped_column(String(30), default="unverified")
    original_message_id: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class OAuthAttempt(Base):
    __tablename__ = "oauth_attempts"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    nonce: Mapped[str] = mapped_column(String(100))
    redirect_uri: Mapped[str] = mapped_column(String(500))
    app_challenge: Mapped[str] = mapped_column(String(43))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exchange_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("sender_user_id", "idempotency_key"),
        Index("ix_reminder_sender_created", "sender_user_id", "created_at"),
        Index("ix_reminder_recipient_status", "recipient_user_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    thread_id: Mapped[str] = mapped_column(ForeignKey("email_threads.id"), index=True)
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    recipient_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_channel: Mapped[str] = mapped_column(
        String(20), default="in_app", server_default="in_app"
    )
    notification_status: Mapped[str] = mapped_column(
        String(30), default="none", server_default="none"
    )
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outbound_message_id: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint_hash: Mapped[str] = mapped_column(String(64), unique=True)
    subscription_encrypted: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EmailPreference(Base):
    __tablename__ = "email_preferences"
    __table_args__ = (UniqueConstraint("sender_user_id", "recipient_email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    recipient_email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_encrypted: Mapped[str] = mapped_column(Text)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExtensionInstallation(Base):
    __tablename__ = "extension_installations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ExtensionSession(Base):
    __tablename__ = "extension_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[str] = mapped_column(String(36))
    session_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecipientPreferences(Base):
    __tablename__ = "recipient_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked_senders: Mapped[list] = mapped_column(JSON, default=list)
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(100))
    # Do not put message bodies, tokens, or email addresses into audit metadata.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RequestBucket(Base):
    __tablename__ = "request_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_id: Mapped[int] = mapped_column(Integer, index=True)
    count: Mapped[int] = mapped_column(Integer)
