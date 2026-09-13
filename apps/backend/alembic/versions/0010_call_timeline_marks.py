"""Call timelines: the states each call moved through and the stages that failed, with when.

A table of its own rather than columns on the call, because a call has as many marks as it has
moves, and diagnosing one reads them in order. Rows go with their call.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_timeline_marks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "call_id",
            sa.String(64),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
    )
    op.create_index("ix_call_timeline_marks_call", "call_timeline_marks", ["call_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_call_timeline_marks_call", table_name="call_timeline_marks")
    op.drop_table("call_timeline_marks")
