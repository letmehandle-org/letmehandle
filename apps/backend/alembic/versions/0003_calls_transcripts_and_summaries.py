"""Calls, their participants, encrypted transcripts, and summaries.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calls",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(32), nullable=False),
        # Who called, sealed: the number and the name, bound to the owner and the call.
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("caller_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("caller_category", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # Whether a line was ever written, which outlives the lines: purged versus never said.
        sa.Column(
            "transcript_recorded", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        # What every transcript and summary points at, so the database refuses a row attached
        # to another user's call.
        sa.UniqueConstraint("id", "user_id", name="uq_calls_id_user"),
    )
    # History: one user's calls, newest first, continued from a cursor on both columns.
    op.create_index("ix_calls_user_started", "calls", ["user_id", "started_at", "id"])

    op.create_table(
        "call_participants",
        sa.Column(
            "call_id",
            sa.String(64),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "call_transcript_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("call_id", sa.String(64), nullable=False),
        # The line's place in its call, bound into the seal, so a gap or a copy is detectable.
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(16), nullable=False),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=False),
        # Which key sealed the row, so a rotation can tell when an old key is no longer needed.
        sa.Column("key_id", sa.String(16), nullable=False),
        # The words, sealed. Nothing else in this table is derived from them.
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id", "user_id"],
            ["calls.id", "calls.user_id"],
            ondelete="CASCADE",
            name="fk_call_transcript_entries_call_user",
        ),
        sa.UniqueConstraint("call_id", "sequence", name="uq_call_transcript_entries_call_sequence"),
    )
    op.create_index(
        "ix_call_transcript_entries_call_said",
        "call_transcript_entries",
        ["call_id", "said_at", "id"],
    )
    # The purge reads and deletes by owner and age.
    op.create_index(
        "ix_call_transcript_entries_user_said",
        "call_transcript_entries",
        ["user_id", "said_at"],
    )

    op.create_table(
        "call_summaries",
        sa.Column("call_id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("escalation_reason", sa.String(48), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("human_joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("key_id", sa.String(16), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["call_id", "user_id"],
            ["calls.id", "calls.user_id"],
            ondelete="CASCADE",
            name="fk_call_summaries_call_user",
        ),
    )


def downgrade() -> None:
    op.drop_table("call_summaries")
    op.drop_index("ix_call_transcript_entries_user_said", table_name="call_transcript_entries")
    op.drop_index("ix_call_transcript_entries_call_said", table_name="call_transcript_entries")
    op.drop_table("call_transcript_entries")
    op.drop_table("call_participants")
    op.drop_index("ix_calls_user_started", table_name="calls")
    op.drop_table("calls")
