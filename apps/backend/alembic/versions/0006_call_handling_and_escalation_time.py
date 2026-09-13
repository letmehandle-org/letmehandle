"""Calls: whom routing gave each to, and when the assistant first asked for the user.

Both nullable. A call still routing was given to nobody yet, a rejected call never is, and a call
the user was never asked for has no moment to record.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("calls", sa.Column("handling", sa.String(16), nullable=True))
    op.add_column("calls", sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("calls", "escalated_at")
    op.drop_column("calls", "handling")
