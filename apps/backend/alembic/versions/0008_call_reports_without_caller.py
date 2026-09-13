"""Handset reports: stop keeping the caller's number beside them in clear.

The number is handed on to the call the report describes, and that call's record seals it. Nothing
ever read it back from here, so the column is dropped rather than sealed.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("call_reports", "caller_number")


def downgrade() -> None:
    # The numbers are not restored: they were dropped so that nothing holds them.
    op.add_column("call_reports", sa.Column("caller_number", sa.String(16), nullable=True))
