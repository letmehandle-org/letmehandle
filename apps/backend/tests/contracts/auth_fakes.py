"""In-memory storage and crypto, for testing the sign-in use case.

The storage really stores, the hasher really hashes, and the rate limiter really counts. A
mock that returned canned answers would let the use case pass while getting rotation, reuse
detection or attempt counting wrong — which are the only parts of this that matter.
"""

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
    DeviceRepository,
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
    from letmehandle.domain.ports.notification import DeviceToken


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


class InMemoryChallengeRepository(OTPChallengeRepository):
    def __init__(self) -> None:
        self.by_id: dict[str, OTPChallenge] = {}

    async def add(self, challenge: OTPChallenge) -> None:
        self.by_id[challenge.id] = challenge

    async def get(self, challenge_id: str) -> OTPChallenge | None:
        return self.by_id.get(challenge_id)

    async def update(self, challenge: OTPChallenge) -> None:
        self.by_id[challenge.id] = challenge

    async def count_issued_since(self, number: PhoneNumber, since: datetime) -> int:
        return sum(
            1
            for challenge in self.by_id.values()
            if challenge.phone_number == number and challenge.issued_at >= since
        )

    async def delete_expired(self, before: datetime) -> int:
        expired = [k for k, v in self.by_id.items() if v.expires_at < before]
        for key in expired:
            del self.by_id[key]
        return len(expired)


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


class InMemoryDeviceRepository(DeviceRepository):
    def __init__(self) -> None:
        self.by_user: dict[str, list[DeviceToken]] = defaultdict(list)

    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        # A token can move between accounts when a handset changes hands, so it is removed from
        # everywhere before being added here. Two accounts sharing one would send somebody
        # else's call context to it.
        for tokens in self.by_user.values():
            if token in tokens:
                tokens.remove(token)
        self.by_user[user_id.value].append(token)

    async def tokens_for(self, user_id: UserId) -> list[DeviceToken]:
        return list(self.by_user[user_id.value])

    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        tokens = self.by_user[user_id.value]
        if token in tokens:
            tokens.remove(token)


class Sha256Hasher(SecretHasher):
    """Fast and constant-time, for tests.

    Not what production uses: a hash this cheap is one worth attacking offline. It is here
    because a test suite that spends a hundred milliseconds per sign-in is a test suite people
    stop running.
    """

    def hash(self, secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()

    def verify(self, secret: str, hashed: str) -> bool:
        return hmac.compare_digest(self.hash(secret), hashed)


class PredictableSecretGenerator(SecretGenerator):
    """Codes and tokens a test can name.

    Predictable on purpose, and unusable in production for exactly that reason — which is why
    it lives under tests and not beside the real one.
    """

    def __init__(self, code: str = "424242") -> None:
        self._code = code
        self._issued = 0

    def numeric_code(self, length: int) -> str:
        return self._code[:length].rjust(length, "0")

    def token(self) -> str:
        self._issued += 1
        return f"refresh-{self._issued}"


class RandomSecretGenerator(SecretGenerator):
    """The real shape: unguessable values from a source fit for secrets."""

    def numeric_code(self, length: int) -> str:
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    def token(self) -> str:
        return secrets.token_urlsafe(32)


class FakeTokenSigner(TokenSigner):
    """Issues a token that is a string and verifies it by looking in a dictionary.

    Enough to exercise the use case. The real signer's own behaviour — signature, expiry,
    tampering — is tested against the real signer.
    """

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

    def set_now(self, instant: datetime) -> None:
        self._now = instant

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

    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        return RateLimitDecision(allowed=True)
