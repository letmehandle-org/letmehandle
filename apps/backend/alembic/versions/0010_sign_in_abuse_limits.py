"""Sign-in codes: close older codes when a new one is sent, and count sends deployment-wide.

`superseded_at` marks a challenge closed by a newer code to the same number, so only the latest
code works. The index on `issued_at` is what the hourly sending budget counts against.

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
    op.add_column(
        "otp_challenges",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_otp_challenges_issued_at", "otp_challenges", ["issued_at"])


def downgrade() -> None:
    op.drop_index("ix_otp_challenges_issued_at", table_name="otp_challenges")
    op.drop_column("otp_challenges", "superseded_at")
