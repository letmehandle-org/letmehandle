"""Sign-in codes: a challenge may hold no hash, where the provider made the code and checks it.

A provider that must send its own registered message makes the code itself, so there is nothing
for this system to hash (D-042). The challenge is still stored, and still carries the expiry, the
attempts and the single use that every limit reads. Relaxing a `NOT NULL` rewrites no rows.

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("otp_challenges", "code_hash", existing_type=sa.String(255), nullable=True)


def downgrade() -> None:
    # Fails, as it should, while any stored challenge's code is held by its provider.
    op.alter_column("otp_challenges", "code_hash", existing_type=sa.String(255), nullable=False)
