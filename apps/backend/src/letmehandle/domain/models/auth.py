"""Signing in: single-use hashed challenges, and refresh tokens that rotate within a family."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber

# Six digits, made strong by the attempt limit and the expiry rather than by length.
CODE_LENGTH = 6
MAX_ATTEMPTS = 5
CHALLENGE_LIFETIME = timedelta(minutes=5)

# How long a rotated refresh token is still honoured, for an app killed before saving the next.
REFRESH_REUSE_LEEWAY = timedelta(minutes=2)


class ChallengeState(StrEnum):
    """Where a challenge is in its short life."""

    PENDING = "pending"
    VERIFIED = "verified"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"
    # A newer code was sent to the same number, so only the latest code works.
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class OTPChallenge:
    """One attempt to prove control of a number; no hash means the provider checks it (D-042)."""

    id: str
    phone_number: PhoneNumber
    code_hash: str | None
    issued_at: datetime
    expires_at: datetime
    attempts: int = 0
    verified_at: datetime | None = None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.code_hash is not None and not self.code_hash.strip():
            raise InvariantError("a challenge with an empty hash can never be satisfied")
        if self.expires_at <= self.issued_at:
            raise InvariantError("a challenge that expires before it is issued is unusable")
        if self.attempts < 0:
            raise InvariantError("attempts cannot be negative")

    def state_at(self, instant: datetime) -> ChallengeState:
        """Where this challenge stands; a used challenge stays verified after it expires."""
        if self.verified_at is not None:
            return ChallengeState.VERIFIED
        if self.superseded_at is not None:
            return ChallengeState.SUPERSEDED
        if self.attempts >= MAX_ATTEMPTS:
            return ChallengeState.EXHAUSTED
        if instant >= self.expires_at:
            return ChallengeState.EXPIRED
        return ChallengeState.PENDING

    def is_open_at(self, instant: datetime) -> bool:
        return self.state_at(instant) is ChallengeState.PENDING

    def with_failed_attempt(self) -> OTPChallenge:
        return replace(self, attempts=self.attempts + 1)

    def verified(self, instant: datetime) -> OTPChallenge:
        if self.verified_at is not None:
            raise InvariantError("a challenge can only be used once")
        return replace(self, attempts=self.attempts + 1, verified_at=instant)

    def superseded(self, instant: datetime) -> OTPChallenge:
        """Closed because a newer code was sent; one verified or superseded is left alone."""
        if self.superseded_at is not None or self.verified_at is not None:
            return self
        return replace(self, superseded_at=instant)

    @property
    def code_is_held_by_provider(self) -> bool:
        """Whether the provider that sent the code, rather than this system, checks it."""
        return self.code_hash is None

    @property
    def failed_attempts(self) -> int:
        """Wrong codes entered against this challenge; the right one, if it came, is not one."""
        return self.attempts - (1 if self.verified_at is not None else 0)


@dataclass(frozen=True, slots=True)
class RefreshToken:
    """A long-lived credential exchanged on use; tokens from one sign-in share a `family_id`."""

    id: str
    family_id: str
    user_id: UserId
    token_hash: str
    issued_at: datetime
    expires_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.token_hash.strip():
            raise InvariantError("a refresh token with no hash can never be presented")
        if self.expires_at <= self.issued_at:
            raise InvariantError("a token that expires before it is issued is unusable")

    def is_usable_at(self, instant: datetime) -> bool:
        return self.rotated_at is None and self.revoked_at is None and instant < self.expires_at

    @property
    def was_already_used(self) -> bool:
        """Whether this token has been exchanged before."""
        return self.rotated_at is not None

    def is_within_reuse_leeway_at(self, instant: datetime) -> bool:
        """Whether this rotated, unrevoked, unexpired token is within `REFRESH_REUSE_LEEWAY`."""
        return (
            self.rotated_at is not None
            and self.revoked_at is None
            and instant < self.expires_at
            and instant - self.rotated_at <= REFRESH_REUSE_LEEWAY
        )

    def rotated(self, instant: datetime) -> RefreshToken:
        return replace(self, rotated_at=instant)

    def revoked(self, instant: datetime) -> RefreshToken:
        return replace(self, revoked_at=self.revoked_at or instant)


@dataclass(frozen=True, slots=True)
class TokenPair:
    """What a successful sign-in hands back."""

    access_token: str
    refresh_token: str
    expires_in_seconds: int

    def __post_init__(self) -> None:
        if not self.access_token.strip() or not self.refresh_token.strip():
            raise InvariantError("a token pair with an empty half is not a sign-in")
        if self.expires_in_seconds <= 0:
            raise InvariantError("an access token that has already expired is not useful")

    def __repr__(self) -> str:
        """The lifetime alone, never either token."""
        return f"TokenPair(expires_in_seconds={self.expires_in_seconds})"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Who a verified access token says you are."""

    user_id: UserId
    issued_at: datetime
    expires_at: datetime

    def is_valid_at(self, instant: datetime) -> bool:
        return self.issued_at <= instant < self.expires_at
