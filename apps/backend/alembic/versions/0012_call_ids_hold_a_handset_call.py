"""Calls: identifiers long enough for a call a handset reported.

Such a call is stored as the account's identifier and the handset's joined together, which is
longer than the 64 characters every call column allowed: each one was refused on its first write,
and the handset's calls never reached history. Widened wherever a call's identifier is kept.
Widening a `varchar` rewrites no rows.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("calls", "id"),
    ("call_participants", "call_id"),
    ("call_transcript_entries", "call_id"),
    ("call_summaries", "call_id"),
    ("escalation_contexts", "call_id"),
    ("call_timeline_marks", "call_id"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(table, column, type_=sa.String(128), existing_type=sa.String(64))


def downgrade() -> None:
    # Fails, as it should, while any stored call's identifier is longer than the old limit.
    for table, column in reversed(_COLUMNS):
        op.alter_column(table, column, type_=sa.String(64), existing_type=sa.String(128))
