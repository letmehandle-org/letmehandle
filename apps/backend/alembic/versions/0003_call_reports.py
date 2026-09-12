"""What handsets report about their own calls.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("screening", sa.String(16), nullable=True),
        sa.Column("ending", sa.String(16), nullable=True),
        sa.Column("caller_number", sa.String(16), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        # Within the user, because a handset's identifiers are only unique to its own account.
        sa.UniqueConstraint("user_id", "event_id", name="uq_call_reports_user_event"),
    )
    op.create_index("ix_call_reports_user_call", "call_reports", ["user_id", "call_id"])


def downgrade() -> None:
    op.drop_index("ix_call_reports_user_call", table_name="call_reports")
    op.drop_table("call_reports")
