"""Google authorization, encrypted credentials, and verified message references."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column("email_threads", sa.Column("original_message_id", sa.String(1000), nullable=True))
    with op.batch_alter_table("connected_accounts") as batch:
        batch.add_column(sa.Column("access_token_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("refresh_token_encrypted", sa.Text(), nullable=True))
        batch.add_column(sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("granted_scopes", sa.JSON(), server_default="[]", nullable=False)
        )
        batch.create_unique_constraint(
            "uq_account_provider_identity", ["provider", "provider_account_id"]
        )
    op.create_table(
        "oauth_attempts",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("verifier_encrypted", sa.Text(), nullable=False),
        sa.Column("nonce", sa.String(100), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=False),
        sa.Column("app_challenge", sa.String(43), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange_hash", sa.String(64), unique=True, nullable=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_index("ix_oauth_attempts_expires_at", "oauth_attempts", ["expires_at"])


def downgrade():
    op.drop_table("oauth_attempts")
    with op.batch_alter_table("connected_accounts") as batch:
        batch.drop_constraint("uq_account_provider_identity", type_="unique")
        for name in (
            "granted_scopes",
            "token_expires_at",
            "refresh_token_encrypted",
            "access_token_encrypted",
        ):
            batch.drop_column(name)
    op.drop_column("email_threads", "original_message_id")
    op.drop_column("users", "session_version")
