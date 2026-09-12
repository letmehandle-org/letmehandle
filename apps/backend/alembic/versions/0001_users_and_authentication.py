"""Users, devices, refresh tokens and one-time challenges.

The first migration. Everything the product needs to know who somebody is, and nothing more:
preferences arrive in phase 3 with their own tables, and calls in phase 8 with theirs.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        # Unique because it is the identity. Stored only in E.164, so two spellings of one
        # number cannot become two accounts.
        sa.Column("phone_number", sa.String(16), nullable=False, unique=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("locale", sa.String(16), nullable=False, server_default="en"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "otp_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("phone_number", sa.String(16), nullable=False),
        # The hash, never the code.
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    # The rate limit is a count over these two columns. Without the index it gets slower as the
    # table grows, which is the opposite of what a limit is for.
    op.create_index(
        "ix_otp_challenges_number_issued", "otp_challenges", ["phone_number", "issued_at"]
    )
    op.create_index("ix_otp_challenges_expires_at", "otp_challenges", ["expires_at"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_user", "refresh_tokens", ["user_id"])

    op.create_table(
        "user_devices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        # A token belongs to one account at a time. A handset that changes hands would
        # otherwise deliver one person's call context to another's phone.
        sa.UniqueConstraint("platform", "token", name="uq_user_devices_platform_token"),
    )
    op.create_index("ix_user_devices_user", "user_devices", ["user_id"])


def downgrade() -> None:
    # Dropped in dependency order. The foreign keys cascade, but relying on that leaves the
    # order to the database rather than stating it.
    op.drop_table("user_devices")
    op.drop_table("refresh_tokens")
    op.drop_table("otp_challenges")
    op.drop_table("users")
