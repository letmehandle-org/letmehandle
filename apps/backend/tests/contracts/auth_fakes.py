"""In-memory storage, hashing, rate limiting and signing that really behave, for sign-in."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from letmehandle.domain.errors import DomainError
from letmehandle.domain.models.auth import AuthenticatedUser
from letmehandle.domain.ports.rate_limit import RateLimitDecision, RateLimiter
from letmehandle.domain.ports.repositories import (
    OTPChallengeRepository,
    RefreshTokenRepository,
    UserRepository,
)
from letmehandle.domain.ports.security import SecretGenerator, SecretHasher, TokenSigner

if TYPE_CHECKING:
    from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.user import User


class InMemoryUserRepository(UserRepository):
    def __init__(self) -> None:
        self.by_id: dict[str, User] = {}

    async def get(self, user_id: UserId) -> User | None:
        return self.by_id.get(user_id.value)

    async def find_by_number(self, number: PhoneNumber) -> User | None:
        return next((user for user in self.by_id.values() if user.phone_number == number), None)

    async def add(self, user: User) -> None:
        self.by_id[user.id.value] = user

    async def update(self, user: User) -> None:
        if user.id.value not in self.by_id:
            raise DomainError("cannot update a user that was never added")
        self.by_id[user.id.value] = user

    async def delete(self, user_id: UserId) -> None:
        self.by_id.pop(user_id.value, None)


class InMemoryChallengeRepository(OTPChallengeRepository):
    def __init__(self) -> None:
        self.by_id: dict[str, OTPChallenge] = {}

    async def add(self, challenge: OTPChallenge) -> None:
        self.by_id[challenge.id] = challenge

    async def get(self, challenge_id: str) -> OTPChallenge | None:
        return self.by_id.get(challenge_id)

    async def update(self, challenge: OTPChallenge) -> None:
        self.by_id[challenge.id] = challenge

    async def issued_since(self, number: PhoneNumber, since: datetime) -> list[datetime]:
        return sorted(
            challenge.issued_at
            for challenge in self.by_id.values()
            if challenge.phone_number == number and challenge.issued_at >= since
        )

    async def failed_attempts_since(self, number: PhoneNumber, since: datetime) -> int:
        return sum(
            challenge.failed_attempts
            for challenge in self.by_id.values()
            if challenge.phone_number == number and challenge.issued_at >= since
        )

    async def supersede_open(self, number: PhoneNumber, instant: datetime) -> int:
        closed = 0
        for key, challenge in list(self.by_id.items()):
            if challenge.phone_number == number and challenge.is_open_at(instant):
                self.by_id[key] = challenge.superseded(instant)
                closed += 1
        return closed

    async def count_all_issued_since(self, since: datetime, calling_code: str | None = None) -> int:
        return sum(
            1
            for challenge in self.by_id.values()
            if challenge.issued_at >= since
            and (calling_code is None or challenge.phone_number.calling_code == calling_code)
        )

    async def delete_expired(self, before: datetime) -> int:
        expired = [k for k, v in self.by_id.items() if v.expires_at < before]
        for key in expired:
            del self.by_id[key]
        return len(expired)

    async def delete_for_number(self, number: PhoneNumber) -> None:
        self.by_id = {
            key: challenge
            for key, challenge in self.by_id.items()
            if challenge.phone_number != number
        }


class InMemoryRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self) -> None:
        self.by_id: dict[str, RefreshToken] = {}

    async def add(self, token: RefreshToken) -> None:
        self.by_id[token.id] = token

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        return next(
            (token for token in self.by_id.values() if token.token_hash == token_hash), None
        )

    async def update(self, token: RefreshToken) -> None:
        self.by_id[token.id] = token

    async def revoke_family(self, family_id: str, at_instant: datetime) -> int:
        revoked = 0
        for key, token in list(self.by_id.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self.by_id[key] = token.revoked(at_instant)
                revoked += 1
        return revoked

    async def revoke_all_for_user(self, user_id: UserId, at_instant: datetime) -> int:
        revoked = 0
        for key, token in list(self.by_id.items()):
            if token.user_id == user_id and token.revoked_at is None:
                self.by_id[key] = token.revoked(at_instant)
                revoked += 1
        return revoked


class Sha256Hasher(SecretHasher):
    """A fast, unsalted, constant-time hasher for tests."""

    def hash(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def verify(self, secret: str, hashed: str) -> bool:
        return hmac.compare_digest(self.hash(secret), hashed)


class SaltedHasher(SecretHasher):
    """A salted hasher whose output differs every time, so no value is found by its hash."""

    def hash(self, secret: str) -> str:
        salt = secrets.token_hex(8)
        digest = hashlib.sha256(f"{salt}{secret}".encode()).hexdigest()
        return f"{salt}${digest}"

    def verify(self, secret: str, hashed: str) -> bool:
        salt, _, expected = hashed.partition("$")
        actual = hashlib.sha256(f"{salt}{secret}".encode()).hexdigest()
        return hmac.compare_digest(actual, expected)


class PredictableSecretGenerator(SecretGenerator):
    """Codes and tokens a test can name in advance."""

    def __init__(self, code: str = "424242") -> None:
        self._code = code
        self._issued = 0

    def numeric_code(self, length: int) -> str:
        return self._code[:length].rjust(length, "0")

    def token(self) -> str:
        self._issued += 1
        return f"refresh-{self._issued}"


class FakeTokenSigner(TokenSigner):
    """Issues a token that is a plain string and verifies it by looking it up in a dictionary."""

    def __init__(self, lifetime: timedelta = timedelta(minutes=15)) -> None:
        self._lifetime = lifetime
        self._issued: dict[str, AuthenticatedUser] = {}
        self._counter = 0

    def issue(self, user_id: UserId, issued_at: datetime) -> tuple[str, datetime]:
        self._counter += 1
        token = f"access-{self._counter}"
        expires_at = issued_at + self._lifetime
        self._issued[token] = AuthenticatedUser(user_id, issued_at, expires_at)
        return token, expires_at

    def verify(self, token: str) -> AuthenticatedUser:
        if token not in self._issued:
            raise DomainError("that token was not issued here")
        return self._issued[token]


class CountingRateLimiter(RateLimiter):
    """Counts per key within a window, and really refuses."""

    def __init__(self, clock_now: datetime | None = None) -> None:
        self._seen: dict[str, list[datetime]] = defaultdict(list)
        self._now = clock_now or datetime.now(tz=UTC)

    @property
    def is_shared(self) -> bool:
        return False

    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        cutoff = self._now - window
        attempts = [seen for seen in self._seen[key] if seen > cutoff]
        attempts.append(self._now)
        self._seen[key] = attempts
        if len(attempts) > limit:
            return RateLimitDecision(allowed=False, retry_after_seconds=int(window.total_seconds()))
        return RateLimitDecision(allowed=True)


class NeverLimits(RateLimiter):
    """Allows everything, for tests about something other than limits."""

    @property
    def is_shared(self) -> bool:
        return True

    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        return RateLimitDecision(allowed=True)
