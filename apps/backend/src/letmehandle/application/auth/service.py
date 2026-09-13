"""Signing in: codes sent behind layered limits, verified, and sessions rotated (D-036, D-042)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import (
    DeliveryUncertainError,
    DomainError,
    InvariantError,
    ProviderError,
)
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.models.auth import (
    CHALLENGE_LIFETIME,
    CODE_LENGTH,
    OTPChallenge,
    RefreshToken,
    TokenPair,
)
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.user import User
from letmehandle.observability import catalogue

# Codes sent, and codes refused before sending, by why.
CHALLENGE_SENT: Final = catalogue.count("auth.challenge.sent")
CHALLENGE_REFUSED: Final = catalogue.count(
    "auth.challenge.refused",
    outcome={"budget", "country_budget", "locked", "number", "source", "unserved_country"},
)

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.clock import Clock, IdGenerator
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.otp import OTPProvider
    from letmehandle.domain.ports.rate_limit import RateLimiter
    from letmehandle.domain.ports.repositories import (
        OTPChallengeRepository,
        RefreshTokenRepository,
        UserRepository,
    )
    from letmehandle.domain.ports.security import SecretGenerator, SecretHasher, TokenSigner


class AuthenticationError(DomainError):
    """Sign-in failed, without saying which part was wrong."""

    failure_kind = FailureKind.NOT_PERMITTED


class UnservedNumberError(DomainError):
    """Codes are not sent to numbers in this country from this deployment."""

    failure_kind = FailureKind.INVALID


class CodeMayHaveBeenSentError(DomainError):
    """The provider never said whether the code went out, so it counts; carries the resend wait."""

    failure_kind = FailureKind.UNAVAILABLE

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("the code may have been sent; try again shortly")
        self.retry_after_seconds = retry_after_seconds


# How long the deployment's budget refuses codes once it is spent.
BUDGET_RETRY_AFTER_SECONDS: Final = int(timedelta(minutes=10).total_seconds())

# How long to wait before offering the same code again when its provider could not check it.
CHECK_RETRY_AFTER_SECONDS: Final = 5


class CodeNotCheckedError(DomainError):
    """The provider holding the code could not check it; no attempt counts and it stays open."""

    failure_kind = FailureKind.UNAVAILABLE

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__("the code could not be checked; try again shortly")
        self.provider = provider
        self.reason = reason
        self.retry_after_seconds = CHECK_RETRY_AFTER_SECONDS


class RateLimitedError(AuthenticationError):
    """Too many attempts. Carries when to try again, so a client does not simply retry."""

    failure_kind = FailureKind.RATE_LIMITED

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many attempts; try again shortly")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AuthenticationPolicy:
    """The configurable limits that govern signing in (D-036)."""

    # Sliding: every renewal starts it again.
    refresh_token_lifetime: timedelta = timedelta(days=90)

    # How long one number waits before another code, by how many it has had today.
    resend_cooldowns: tuple[timedelta, ...] = (
        timedelta(seconds=30),
        timedelta(seconds=60),
        timedelta(minutes=2),
        timedelta(minutes=5),
    )
    challenges_per_number: int = 5
    challenges_per_number_window: timedelta = timedelta(hours=1)
    challenges_per_number_per_day: int = 10

    # Wrong codes one number may have, across every code sent to it, before it is locked.
    failed_codes_per_number: int = 10
    failed_codes_window: timedelta = timedelta(hours=24)

    challenges_per_source: int = 20
    challenges_per_source_window: timedelta = timedelta(hours=1)
    verifications_per_source: int = 60
    verifications_per_source_window: timedelta = timedelta(hours=1)

    # Calling codes a code may be sent to, without the plus; `None` sends anywhere.
    allowed_calling_codes: frozenset[str] | None = None
    # The deployment's hourly budget, overall and for any one calling code. `None` is unbounded.
    challenges_per_hour: int | None = 500
    challenges_per_hour_per_calling_code: int | None = 100

    @property
    def retention(self) -> timedelta:
        """How long a challenge must be kept to be counted by every limit that reads it."""
        return max(
            self.challenges_per_number_window,
            timedelta(days=1),
            self.failed_codes_window,
            CHALLENGE_LIFETIME,
        )


@dataclass(frozen=True, slots=True)
class ChallengeIssued:
    """What the caller gets back when a code has been sent, and nothing about the account."""

    challenge_id: str
    expires_in_seconds: int
    # Seconds until this number may be sent another code.
    resend_after_seconds: int


class AuthenticationService:
    """The sign-in use case."""

    def __init__(
        self,
        *,
        users: UserRepository,
        challenges: OTPChallengeRepository,
        refresh_tokens: RefreshTokenRepository,
        otp: OTPProvider,
        code_hasher: SecretHasher,
        token_hasher: SecretHasher,
        secrets: SecretGenerator,
        signer: TokenSigner,
        clock: Clock,
        ids: IdGenerator,
        rate_limiter: RateLimiter,
        policy: AuthenticationPolicy | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._users = users
        self._challenges = challenges
        self._refresh_tokens = refresh_tokens
        self._otp = otp
        # Codes are verified with a salted slow hash; tokens are found by a keyed deterministic one.
        self._code_hasher = code_hasher
        self._token_hasher = token_hasher
        self._secrets = secrets
        self._signer = signer
        self._clock = clock
        self._ids = ids
        self._rate_limiter = rate_limiter
        self._policy = policy or AuthenticationPolicy()
        self._metrics = metrics

    # ------------------------------------------------------------- requesting

    async def request_challenge(
        self, number: PhoneNumber, *, source: str | None = None
    ) -> ChallengeIssued:
        """Send a code to the number once every sending limit allows it (D-036)."""
        now = self._clock.now()
        policy = self._policy

        allowed = policy.allowed_calling_codes
        if allowed is not None and number.calling_code not in allowed:
            self._refused("unserved_country")
            raise UnservedNumberError("codes are not sent to numbers in this country")

        if source is not None:
            decision = await self._rate_limiter.check(
                f"challenge:source:{source}",
                limit=policy.challenges_per_source,
                window=policy.challenges_per_source_window,
            )
            if not decision.allowed:
                self._refused("source")
                raise RateLimitedError(decision.retry_after_seconds)

        await self._refuse_if_locked(number, now)

        issued = await self._challenges.issued_since(number, now - timedelta(days=1))
        wait = self._wait_for_another(issued, now)
        if wait > 0:
            self._refused("number")
            raise RateLimitedError(wait)

        await self._refuse_over_budget(number, now)

        # None where the provider makes the code (D-042).
        code = None if self._otp.issues_its_own_codes(number) else self._challenge_code()
        challenge = OTPChallenge(
            id=self._ids.generate(),
            phone_number=number,
            code_hash=None if code is None else self._code_hasher.hash(code),
            issued_at=now,
            expires_at=now + CHALLENGE_LIFETIME,
        )
        # Only the latest code works.
        await self._challenges.supersede_open(number, now)
        await self._challenges.add(challenge)

        # Sent after the challenge is stored, so a delivered code is always acceptable.
        try:
            if code is None:
                await self._otp.send_own_code(number)
            else:
                await self._otp.send(number, code)
        except DeliveryUncertainError as error:
            # Kept and counted, since it may have been delivered (D-037).
            raise CodeMayHaveBeenSentError(self._wait_for_another([*issued, now], now)) from error
        if self._metrics is not None:
            self._metrics.increment(CHALLENGE_SENT)

        return ChallengeIssued(
            challenge_id=challenge.id,
            expires_in_seconds=int(CHALLENGE_LIFETIME.total_seconds()),
            resend_after_seconds=self._wait_for_another([*issued, now], now),
        )

    def _wait_for_another(self, issued: list[datetime], now: datetime) -> int:
        """Seconds until another code: the longest of the cooldown, hourly and daily waits."""
        policy = self._policy
        if not issued:
            return 0
        waits: list[float] = []

        cooldowns = policy.resend_cooldowns
        cooldown = cooldowns[min(len(issued), len(cooldowns)) - 1]
        waits.append((issued[-1] + cooldown - now).total_seconds())

        hour = [at for at in issued if at >= now - policy.challenges_per_number_window]
        if len(hour) >= policy.challenges_per_number:
            oldest = hour[-policy.challenges_per_number]
            waits.append((oldest + policy.challenges_per_number_window - now).total_seconds())

        if len(issued) >= policy.challenges_per_number_per_day:
            oldest = issued[-policy.challenges_per_number_per_day]
            waits.append((oldest + timedelta(days=1) - now).total_seconds())

        longest = max(waits)
        return 0 if longest <= 0 else max(1, int(-(-longest // 1)))

    async def _refuse_if_locked(self, number: PhoneNumber, now: datetime) -> None:
        """Too many wrong codes for this number lately: no new code, and no code accepted."""
        policy = self._policy
        failed = await self._challenges.failed_attempts_since(
            number, now - policy.failed_codes_window
        )
        if failed >= policy.failed_codes_per_number:
            self._refused("locked")
            # The whole window, which says nothing about how the failures are spread.
            raise RateLimitedError(int(policy.failed_codes_window.total_seconds()))

    async def _refuse_over_budget(self, number: PhoneNumber, now: datetime) -> None:
        """Refuse once the deployment's hourly budget, overall or for this country, is spent."""
        policy = self._policy
        hour_ago = now - timedelta(hours=1)
        if (
            policy.challenges_per_hour is not None
            and await self._challenges.count_all_issued_since(hour_ago)
            >= policy.challenges_per_hour
        ):
            self._refused("budget")
            raise RateLimitedError(BUDGET_RETRY_AFTER_SECONDS)
        if (
            policy.challenges_per_hour_per_calling_code is not None
            and await self._challenges.count_all_issued_since(hour_ago, number.calling_code)
            >= policy.challenges_per_hour_per_calling_code
        ):
            self._refused("country_budget")
            raise RateLimitedError(BUDGET_RETRY_AFTER_SECONDS)

    def _refused(self, reason: str) -> None:
        if self._metrics is not None:
            self._metrics.increment(CHALLENGE_REFUSED, {"outcome": reason})

    def _challenge_code(self) -> str:
        """A new challenge's code: random, or fixed only by a provider unsafe for production."""
        fixed = self._otp.fixed_code
        if fixed is None:
            return self._secrets.numeric_code(CODE_LENGTH)
        if self._otp.is_safe_for_production:
            raise InvariantError(
                f"{self._otp.name} delivers real codes and must not fix them; a fixed code is "
                "for a testing provider only"
            )
        if len(fixed) != CODE_LENGTH or not fixed.isdigit():
            raise InvariantError(f"a fixed code must be {CODE_LENGTH} digits")
        return fixed

    # -------------------------------------------------------------- verifying

    async def verify(self, challenge_id: str, code: str, *, source: str | None = None) -> TokenPair:
        """Exchange a correct code for a session, creating the account if there is none."""
        now = self._clock.now()
        if source is not None:
            decision = await self._rate_limiter.check(
                f"verify:source:{source}",
                limit=self._policy.verifications_per_source,
                window=self._policy.verifications_per_source_window,
            )
            if not decision.allowed:
                raise RateLimitedError(decision.retry_after_seconds)

        challenge = await self._challenges.get(challenge_id)

        if challenge is None or not challenge.is_open_at(now):
            raise AuthenticationError("that code is not valid")

        await self._refuse_if_locked(challenge.phone_number, now)

        if not await self._is_the_code(challenge, code):
            # A wrong code consumes an attempt.
            await self._challenges.update(challenge.with_failed_attempt())
            raise AuthenticationError("that code is not valid")

        await self._challenges.update(challenge.verified(now))

        user = await self._users.find_by_number(challenge.phone_number)
        if user is None:
            user = User(id=UserId(self._ids.generate()), phone_number=challenge.phone_number)
            await self._users.add(user)

        return await self._issue_pair(user.id, family_id=self._ids.generate(), now=now)

    async def _is_the_code(self, challenge: OTPChallenge, code: str) -> bool:
        """Whether `code` is the challenge's, by its hash or by its provider, under the row lock."""
        if challenge.code_hash is not None:
            return self._code_hasher.verify(code, challenge.code_hash)
        number = challenge.phone_number
        if not self._otp.issues_its_own_codes(number):
            # No provider configured now holds this code, so it can never be satisfied.
            return False
        try:
            return await self._otp.check(number, code)
        except ProviderError as error:
            raise CodeNotCheckedError(error.provider, error.reason) from error

    # -------------------------------------------------------------- refreshing

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair; reuse outside the leeway revokes its family."""
        now = self._clock.now()
        stored = await self._refresh_tokens.find_by_hash(self._token_hasher.hash(refresh_token))

        if stored is None:
            raise AuthenticationError("that session is not valid")

        if stored.was_already_used:
            if stored.is_within_reuse_leeway_at(now):
                # Within REFRESH_REUSE_LEEWAY: a new pair, and nothing revoked.
                return await self._issue_pair(stored.user_id, family_id=stored.family_id, now=now)
            await self._refresh_tokens.revoke_family(stored.family_id, now)
            raise AuthenticationError("that session is not valid")

        if not stored.is_usable_at(now):
            raise AuthenticationError("that session is not valid")

        await self._refresh_tokens.update(stored.rotated(now))
        return await self._issue_pair(stored.user_id, family_id=stored.family_id, now=now)

    # --------------------------------------------------------------- ending it

    async def sign_out(self, refresh_token: str) -> UserId | None:
        """End this session's family, returning whose it was, or nothing for an unknown token."""
        stored = await self._refresh_tokens.find_by_hash(self._token_hasher.hash(refresh_token))
        if stored is None:
            return None
        await self._refresh_tokens.revoke_family(stored.family_id, self._clock.now())
        return stored.user_id

    # ----------------------------------------------------------------- shared

    async def _issue_pair(self, user_id: UserId, *, family_id: str, now: datetime) -> TokenPair:
        access_token, expires_at = self._signer.issue(user_id, now)
        raw_refresh = self._secrets.token()

        await self._refresh_tokens.add(
            RefreshToken(
                id=self._ids.generate(),
                family_id=family_id,
                user_id=user_id,
                token_hash=self._token_hasher.hash(raw_refresh),
                issued_at=now,
                expires_at=now + self._policy.refresh_token_lifetime,
            )
        )

        return TokenPair(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in_seconds=int((expires_at - now).total_seconds()),
        )


async def forget_spent_challenges(
    challenges: OTPChallengeRepository, clock: Clock, policy: AuthenticationPolicy | None = None
) -> int:
    """Delete the challenges no limit counts any more, returning how many went."""
    window = (policy or AuthenticationPolicy()).retention
    return await challenges.delete_expired(clock.now() - window)
