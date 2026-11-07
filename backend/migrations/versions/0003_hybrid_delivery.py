"""Hybrid reminder delivery, push subscriptions, and email opt-outs."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "reminders",
        sa.Column("delivery_channel", sa.String(20), server_default="in_app", nullable=False),
    )
    op.add_column(
        "reminders",
        sa.Column("notification_status", sa.String(30), server_default="none", nullable=False),
    )
    op.add_column("reminders", sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reminders", sa.Column("outbound_message_id", sa.String(1000), nullable=True))
    op.add_column("reminders", sa.Column("provider_message_id", sa.String(200), nullable=True))
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("endpoint_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("subscription_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])
    op.create_table(
        "email_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sender_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recipient_email", sa.String(320), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("sender_user_id", "recipient_email"),
    )


def downgrade():
    op.drop_table("email_preferences")
    op.drop_table("push_subscriptions")
    for name in (
        "provider_message_id",
        "outbound_message_id",
        "attempted_at",
        "notification_status",
        "delivery_channel",
    ):
        op.drop_column("reminders", name)
