"""The database schema.

These are adapter types. They never cross into the domain: a repository maps between them and
the domain's own types at its edge, so a change of column never reaches the rules.

Two conventions worth stating, because they are decisions rather than style:

  Times are stored with their timezone. A naive column means whatever the server was set to
  when the row was written, and a service moved between regions cannot tell what that was.

  Nothing here has a `deleted` flag. Deletion deletes. A product that promises to forget
  things and keeps a hidden copy has not forgotten them.
"""

from __future__ import annotations

# Not moved into a type-checking block, whatever the linter says: SQLAlchemy's
# declarative mapper evaluates these annotations at run time to build the columns.
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """The declarative base. Alembic reads its metadata to find drift."""


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # The identity, and therefore unique. Stored in E.164 and only in E.164: normalisation
    # happens before anything reaches here, so two spellings cannot become two accounts.
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # server_default as well as a Python default, so the column is correct even for a row
    # written by something that is not this application — a migration, or a fix by hand.
    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en", server_default="en"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OTPChallengeRow(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phone_number: Mapped[str] = mapped_column(String(16), nullable=False)
    # The hash, never the code. Nothing in this system can say what the code was.
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The rate limit counts challenges per number within a window, which is this index.
        # Without it the limit gets slower as the table grows, which is exactly backwards.
        Index("ix_otp_challenges_number_issued", "phone_number", "issued_at"),
        Index("ix_otp_challenges_expires_at", "expires_at"),
    )


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Every token descended from one sign-in shares this. Revoking a family is one statement
    # against this column, which is what makes reuse detection cheap enough to always do.
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Unique, and the only way a token is found. A keyed hash rather than a salted one, so the
    # lookup is an index rather than a scan of every row.
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
    """How one user wants their calls handled.

    Stored as a document rather than as a table per section. The shape is read and written whole
    — the application composes a complete set and saves it — and it is never queried across
    users, so a dozen joined tables would buy nothing and cost a migration every time a
    preference is added.

    `version` is what makes that safe: it records the shape the document was written in, so a
    later change can migrate what is there instead of guessing whether an absent field means
    the user declined or the field did not exist when they answered.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OnboardingRow(Base):
    """How far through setting up a user is.

    Its own table rather than a field on the preferences document: progress changes on every
    step while preferences change rarely, and a user who skips a step has no preferences to
    write for it. Keeping them together would mean writing a whole document to record a tap.
    """

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
        # A token belongs to one account at a time. A handset that changes hands would
        # otherwise deliver one person's call context to another's phone.
        UniqueConstraint("platform", "token", name="uq_user_devices_platform_token"),
        Index("ix_user_devices_user", "user_id"),
    )


class CallRow(Base):
    """One call: its state, its caller and its timing.

    Relational rather than a document, unlike preferences (D-022). A call is queried across time
    — a user's history, newest first, a page at a time — so what is filtered and sorted on is a
    column with an index behind it. Nothing anybody said is here.
    """

    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # E.164, or nothing for a withheld number.
    caller_number: Mapped[str | None] = mapped_column(String(16), nullable=True)
    caller_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    caller_category: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # The target of every foreign key below. A transcript or summary names the call *and*
        # its owner, so the database itself refuses a row attached to somebody else's call —
        # isolation that holds even for a query that forgot to filter.
        UniqueConstraint("id", "user_id", name="uq_calls_id_user"),
        # History: one user's calls, newest first, continued from a cursor on both columns.
        Index("ix_calls_user_started", "user_id", "started_at", "id"),
    )


class CallParticipantRow(Base):
    """Who was on a call, in the order they joined.

    Read and written whole with its call. `position` is the order of joining, which is what a
    role that leaves and rejoins needs to stay two entries rather than one.
    """

    __tablename__ = "call_participants"

    call_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("calls.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TranscriptEntryRow(Base):
    """One thing somebody said, encrypted (D-014).

    `ciphertext` is the only column derived from the words, and it is sealed at the application
    layer with the user, call, speaker and moment as authenticated context — so a database dump
    is not a transcript dump, and a row copied onto another call does not open. No column, index
    or constraint here is computed from the text.
    """

    __tablename__ = "call_transcript_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
        # Reading one call's transcript in the order it was said.
        Index("ix_call_transcript_entries_call_said", "call_id", "said_at", "id"),
        # The purge: which users hold old entries, and each one's oldest entries first.
        Index("ix_call_transcript_entries_user_said", "user_id", "said_at"),
    )


class CallSummaryRow(Base):
    """The structured record of a call, written once, which outlives its transcript.

    What is filtered on is a column: outcome, intent, importance and timing, all enumerations or
    instants. What was said about the call — headline, extracted details and the evidence
    quoting the transcript, and who the caller was taken to be — is sealed like a transcript.
    """

    __tablename__ = "call_summaries"

    call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
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
