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
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
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


class EscalationContextRow(Base):
    """What a user was told about one escalation, kept so the app can fetch it without a push.

    Keyed by the user and the call together. The call id alone would let one user's escalation
    stand in the way of another's, and every read filters by both anyway.

    Nothing here is a transcript or a recording: a label for the caller, two short sentences, and
    what became of the notification.
    """

    __tablename__ = "escalation_contexts"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    caller_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    established: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    needed: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (PrimaryKeyConstraint("user_id", "call_id", name="pk_escalation_contexts"),)
