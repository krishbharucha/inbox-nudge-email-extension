"""Shared, short-lived request counters; no raw client addresses are stored."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_buckets",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("window_id", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_request_buckets_window_id", "request_buckets", ["window_id"])


def downgrade():
    op.drop_table("request_buckets")
