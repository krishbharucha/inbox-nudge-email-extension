"""Extension connections and rotating, hashed application refresh sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "extension_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_extension_installations_user_id", "extension_installations", ["user_id"])
    op.create_table(
        "extension_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("installation_id", sa.String(36), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in ("family_id", "user_id", "expires_at"):
        op.create_index("ix_extension_sessions_" + column, "extension_sessions", [column])


def downgrade():
    op.drop_table("extension_sessions")
    op.drop_table("extension_installations")
