"""Signing in, as the domain sees it.

The product's identity is a phone number, so signing in means proving you can receive a call or
a message at one. Everything here exists to make that proof hard to forge and impossible to
replay.

Two ideas do most of the work:

  A challenge is single use, expiring and attempt-limited, and the code is never stored. What
  is stored is a hash, so a database that leaks does not hand out sign-ins. Where the provider
  makes and checks the code itself, nothing about the code is stored at all (D-042).

  A refresh token belongs to a family. Rotating one invalidates it; presenting a rotated one
  again means somebody has a copy, and the whole family is revoked. That converts a stolen
  token from indefinite access into one use and an alarm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber

# Six digits is what people can hold in their head between two applications. The strength comes
# from the attempt limit and the expiry, not from the length: a million combinations is nothing
# to a machine and everything to one with five tries and five minutes.
CODE_LENGTH = 6
MAX_ATTEMPTS = 5
CHALLENGE_LIFETIME = timedelta(minutes=5)

# How long after rotating a refresh token the one it replaced is still honoured.
#
# The server rotates a token and answers; the phone then writes the new one to its keychain. An app
# killed between the two — by the system, a crash, a flat battery — comes back holding the old one,
# and without this it would present it, trip reuse detection and lose the session: a user asked for
# their number again for no fault of their own. Two minutes covers that and a retry. A thief would
# need a copy within the same two minutes; outside them reuse still revokes the whole family.
REFRESH_REUSE_LEEWAY = timedelta(minutes=2)


class ChallengeState(StrEnum):
    """Where a challenge is in its short life."""

    PENDING = "pending"
    VERIFIED = "verified"
    EXHAUSTED = "exhausted"
    EXPIRED = "expired"
    # A newer code was sent to the same number. Only the latest code works, so asking for codes
    # cannot open several at once to guess against in parallel.
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class OTPChallenge:
    """One attempt to prove control of a number.

    `code_hash` rather than the code. Nothing in this system can tell anybody what the code was,
    including the system itself, which is the property that makes a leaked database useless for
    signing in. No hash at all means the provider made the code and is the one that checks it
    (D-042); every other rule here — expiry, attempts, single use — applies to it unchanged.
    """

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
        """Where this challenge stands, as of now.

        Order matters: a challenge that has been used is verified even after it expires, so
        that a replay is refused as "already used" rather than "too late", and the two are
        distinguishable in the logs.
        """
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
        return OTPChallenge(
            id=self.id,
            phone_number=self.phone_number,
            code_hash=self.code_hash,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            attempts=self.attempts + 1,
            verified_at=self.verified_at,
            superseded_at=self.superseded_at,
        )

    def verified(self, instant: datetime) -> OTPChallenge:
        if self.verified_at is not None:
            raise InvariantError("a challenge can only be used once")
        return OTPChallenge(
            id=self.id,
            phone_number=self.phone_number,
            code_hash=self.code_hash,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            attempts=self.attempts + 1,
            verified_at=instant,
            superseded_at=self.superseded_at,
        )

    def superseded(self, instant: datetime) -> OTPChallenge:
        """Closed because a newer code was sent. A challenge already finished is left as it was."""
        if self.superseded_at is not None or self.verified_at is not None:
            return self
        return OTPChallenge(
            id=self.id,
            phone_number=self.phone_number,
            code_hash=self.code_hash,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            attempts=self.attempts,
            verified_at=self.verified_at,
            superseded_at=instant,
        )

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
    """A long-lived credential that is exchanged rather than reused.

    `family_id` ties every token descended from one sign-in together. Rotation replaces a token
    with its successor; if the replaced one is ever presented again, either the user's copy was
    stolen or the thief's was, and there is no way to tell which — so the whole family goes.
    The legitimate user signs in again; the thief gets nothing.
    """

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
        """Whether this token has been exchanged before.

        Presenting one of these is the signal that a copy exists somewhere it should not.
        """
        return self.rotated_at is not None

    def is_within_reuse_leeway_at(self, instant: datetime) -> bool:
        """Whether this token was rotated so recently that presenting it again is not theft.

        Only a token that was rotated, never revoked and has not expired. See
        `REFRESH_REUSE_LEEWAY` for why the window exists and why it is short.
        """
        return (
            self.rotated_at is not None
            and self.revoked_at is None
            and instant < self.expires_at
            and instant - self.rotated_at <= REFRESH_REUSE_LEEWAY
        )

    def rotated(self, instant: datetime) -> RefreshToken:
        return RefreshToken(
            id=self.id,
            family_id=self.family_id,
            user_id=self.user_id,
            token_hash=self.token_hash,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            rotated_at=instant,
            revoked_at=self.revoked_at,
        )

    def revoked(self, instant: datetime) -> RefreshToken:
        return RefreshToken(
            id=self.id,
            family_id=self.family_id,
            user_id=self.user_id,
            token_hash=self.token_hash,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            rotated_at=self.rotated_at,
            revoked_at=self.revoked_at or instant,
        )


@dataclass(frozen=True, slots=True)
class TokenPair:
    """What a successful sign-in hands back.

    The access token is not stored anywhere: it is verified by signature, and its short life is
    what limits the damage of a leak. The refresh token is stored as a hash, for the same
    reason challenge codes are.
    """

    access_token: str
    refresh_token: str
    expires_in_seconds: int

    def __post_init__(self) -> None:
        if not self.access_token.strip() or not self.refresh_token.strip():
            raise InvariantError("a token pair with an empty half is not a sign-in")
        if self.expires_in_seconds <= 0:
            raise InvariantError("an access token that has already expired is not useful")

    def __repr__(self) -> str:
        """Neither token.

        A token pair in a log line is a sign-in somebody can replay.
        """
        return f"TokenPair(expires_in_seconds={self.expires_in_seconds})"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Who a verified access token says you are."""

    user_id: UserId
    issued_at: datetime
    expires_at: datetime

    def is_valid_at(self, instant: datetime) -> bool:
        return self.issued_at <= instant < self.expires_at
