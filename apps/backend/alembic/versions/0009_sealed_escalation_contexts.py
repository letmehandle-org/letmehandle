"""Escalation contexts: seal what the user was told, as transcripts and summaries are sealed.

The caller's label and the two sentences become one ciphertext under the transcript keys (D-014).
A migration holds no key, so what is already stored cannot be sealed here: those words are dropped
rather than kept in clear. A context is read while its call is fresh, and its reason, status and
delivery stay.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_WORDS = (("caller_label", 120), ("established", 1000), ("needed", 1000))


def upgrade() -> None:
    for name, _ in _WORDS:
        op.drop_column("escalation_contexts", name)
    op.add_column("escalation_contexts", sa.Column("key_id", sa.String(16), nullable=True))
    op.add_column("escalation_contexts", sa.Column("ciphertext", sa.LargeBinary(), nullable=True))
    op.create_check_constraint(
        "ck_escalation_contexts_sealed",
        "escalation_contexts",
        "(key_id IS NULL) = (ciphertext IS NULL)",
    )


def downgrade() -> None:
    # The words are not restored: opening them needs the keys, which a migration never holds.
    op.drop_constraint("ck_escalation_contexts_sealed", "escalation_contexts", type_="check")
    op.drop_column("escalation_contexts", "ciphertext")
    op.drop_column("escalation_contexts", "key_id")
    for name, length in _WORDS:
        op.add_column("escalation_contexts", sa.Column(name, sa.String(length), nullable=True))
