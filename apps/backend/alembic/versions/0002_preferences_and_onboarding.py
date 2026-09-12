"""Preferences and onboarding progress.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # The shape the document was written in, so a later change can migrate what is there
        # rather than guess whether an absent field means the user declined or the field did
        # not exist when they answered.
        sa.Column("version", sa.Integer(), nullable=False),
        # One document rather than a table per section: it is read and written whole, and it is
        # never queried across users, so a dozen joined tables would buy nothing and cost a
        # migration every time a preference is added.
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "user_onboarding",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("completed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("skipped", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding")
    op.drop_table("user_preferences")
