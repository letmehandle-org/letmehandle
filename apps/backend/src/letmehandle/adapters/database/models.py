"""The database schema: adapter types, with timezone-aware times and no soft deletes."""

from __future__ import annotations

# Kept out of a type-checking block: SQLAlchemy evaluates these annotations at run time.
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.expression import false, text

# Holds an account id, a separator and a handset's call id together (`scoped_call_id`).
CALL_ID_LENGTH = 128


class Base(DeclarativeBase):
    """The declarative base. Alembic reads its metadata to find drift."""


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The identity, unique, stored only in E.164.
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # A server default too, so rows written outside this application get the value.
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en", server_default="en"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OTPChallengeRow(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False)
    # The hash, never the code; null when the provider makes and checks the code (D-042).
    code_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a newer code to the same number closed this one.
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Counts challenges per number within the rate-limit window.
        Index("ix_otp_challenges_number_issued", "phone_number", "issued_at"),
        Index("ix_otp_challenges_expires_at", "expires_at"),
        # The deployment's own sending budget counts every challenge in the last hour.
        Index("ix_otp_challenges_issued_at", "issued_at"),
    )


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Shared by every token from one sign-in, so a family is revoked in one statement.
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The unique lookup key: a keyed hash, so a token is found through an index.
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_refresh_tokens_family", "family_id"),
        Index("ix_refresh_tokens_user", "user_id"),
    )


class PreferencesRow(Base):
    """How one user wants their calls handled, stored as one versioned document (D-022)."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OnboardingRow(Base):
    """How far through setting up a user is, kept apart from the preferences document."""

    __tablename__ = "user_onboarding"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    completed: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    skipped: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeviceRow(Base):
    __tablename__ = "user_devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A token belongs to one account at a time.
        UniqueConstraint("platform", "token", name="uq_user_devices_platform_token"),
        Index("ix_user_devices_user", "user_id"),
    )


class CallRow(Base):
    """One call: its state, its sealed caller (D-014) and its timing, as indexed columns."""

    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(CALL_ID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # The key id, and the caller's number and name sealed with owner and call as context.
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    caller_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    caller_category: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whom routing gave the call to, and when the assistant first asked for the user.
    handling: Mapped[str | None] = mapped_column(String(16), nullable=True)
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set by the first transcript line and never cleared: tells a purged transcript from none.
    transcript_recorded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        # The target of the composite foreign keys: a child row cannot name another user's call.
        UniqueConstraint("id", "user_id", name="uq_calls_id_user"),
        # History: one user's calls, newest first, continued from a cursor on both columns.
        Index("ix_calls_user_started", "user_id", "started_at", "id"),
        # Recovery after a restart: the calls nothing has ended, which are few among all calls.
        Index(
            "ix_calls_unfinished",
            "started_at",
            "id",
            postgresql_where=text("ended_at IS NULL"),
        ),
    )


class CallParticipantRow(Base):
    """Who was on a call, in joining order; a role that rejoins is a second entry."""

    __tablename__ = "call_participants"

    call_id: Mapped[str] = mapped_column(
        String(CALL_ID_LENGTH), ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallTimelineMarkRow(Base):
    """One mark in a call's timeline: a state it entered or a stage that failed, and when."""

    __tablename__ = "call_timeline_marks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_call_timeline_marks_call", "call_id", "id"),)


class TranscriptEntryRow(Base):
    """One thing somebody said, sealed with user, call, position, speaker and moment (D-014)."""

    __tablename__ = "call_transcript_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[str] = mapped_column(String(CALL_ID_LENGTH), nullable=False)
    # The line's place in its call, in the order lines were written, bound into the seal.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    said_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["call_id", "user_id"],
            ["calls.id", "calls.user_id"],
            ondelete="CASCADE",
            name="fk_call_transcript_entries_call_user",
        ),
        # One line per place in a call, so a copied row cannot take a number already taken.
        UniqueConstraint("call_id", "sequence", name="uq_call_transcript_entries_call_sequence"),
        # Reading one call's transcript in the order it was said.
        Index("ix_call_transcript_entries_call_said", "call_id", "said_at", "id"),
        # The purge: which users hold old entries, and each one's oldest entries first.
        Index("ix_call_transcript_entries_user_said", "user_id", "said_at"),
    )


class CallSummaryRow(Base):
    """The structured record of a call, which outlives its transcript; its content is sealed."""

    __tablename__ = "call_summaries"

    call_id: Mapped[str] = mapped_column(String(CALL_ID_LENGTH), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    intent: Mapped[str] = mapped_column(String(32), nullable=False)
    importance: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    human_joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    key_id: Mapped[str] = mapped_column(String(16), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["call_id", "user_id"],
            ["calls.id", "calls.user_id"],
            ondelete="CASCADE",
            name="fk_call_summaries_call_user",
        ),
    )


class EscalationContextRow(Base):
    """What a user was told about one escalation, keyed by user and call, words sealed (D-014)."""

    __tablename__ = "escalation_contexts"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String(CALL_ID_LENGTH), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "call_id", name="pk_escalation_contexts"),
        CheckConstraint(
            "(key_id IS NULL) = (ciphertext IS NULL)", name="ck_escalation_contexts_sealed"
        ),
    )


class CallReportRow(Base):
    """One event a user's handset reported about a call, unique per user; never the caller."""

    __tablename__ = "call_reports"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    screening: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ending: Mapped[str | None] = mapped_column(String(16), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_call_reports_user_event"),
        Index("ix_call_reports_user_call", "user_id", "call_id"),
    )
