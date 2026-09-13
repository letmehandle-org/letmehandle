"""Escalation contexts: what the user was told about each escalation.

The device table from 0001 already holds a push token per platform, so delivery needs no change
to it. This adds only what push cannot be relied on to carry: the context, readable again.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escalation_contexts",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("caller_label", sa.String(120), nullable=True),
        sa.Column("established", sa.String(1000), nullable=True),
        sa.Column("needed", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("delivery", sa.String(16), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        # The user and the call together: one user's call id can never block another's.
        sa.PrimaryKeyConstraint("user_id", "call_id", name="pk_escalation_contexts"),
    )


def downgrade() -> None:
    op.drop_table("escalation_contexts")
