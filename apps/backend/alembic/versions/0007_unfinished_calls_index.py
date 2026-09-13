"""Calls: find the ones nothing has ended, which a process starting after a restart ends.

Partial, because unfinished calls are a handful among every call ever made, and startup should not
read the whole table to find them.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_calls_unfinished",
        "calls",
        ["started_at", "id"],
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_calls_unfinished", table_name="calls")
