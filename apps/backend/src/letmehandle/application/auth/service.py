"""Signing in.

The use case, composed from ports. It contains no SQL, no HTTP and no vendor, and every
decision it makes is one the domain model already knows how to express.

Two properties are worth stating plainly because they are easy to lose in a refactor:

  A failure never says which part was wrong. "No such account" and "wrong code" are the same
  response, because the difference tells an attacker which numbers are worth attacking.

  Verifying a code consumes an attempt whether or not the code was right, and whether or not
  the challenge existed. Otherwise the attempt limit is advisory.

Every code sent costs money and reaches a phone, so sending is defended in layers, each against a
different attack, and each counted where that attack cannot reset it (D-036):

  1. Which countries codes go to at all — premium-rate and unserved destinations never get one.
  2. How many one source may ask for — one place asking for codes to many numbers.
  3. Whether the number is locked after too many wrong codes — guessing, across new codes.
  4. How soon, and how often, one number may be sent another — bombing somebody's phone.
  5. How many the whole deployment, and each country, sends in an hour — the budget an attack
     that spreads across numbers and sources to earn from the messages eventually meets.

Only the latest code to a number works, so asking for more codes never opens more to guess at.

Where a provider makes and checks a number's code itself (D-042), only the comparison moves to it.
Every layer above, the expiry, the attempt limits and single use are still decided here, and a
provider that cannot say whether a code is right never signs anybody in.
"""

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

# A sign-in code sent, and one refused before it was: the counts to alert on when codes are pumped.
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
    """Sign-in failed.

    Deliberately one type. Distinguishing "no such account" from "wrong code" in the response
    tells an attacker which numbers have accounts, and that is the expensive half of attacking
    a phone-number identity.
    """

    failure_kind = FailureKind.NOT_PERMITTED


class UnservedNumberError(DomainError):
    """Codes are not sent to numbers in this country from this deployment.

    Not a sign-in failure: the number was never tried, and saying so tells nobody anything about
    an account, since it is true of every number with that calling code.
    """

    failure_kind = FailureKind.INVALID


class CodeMayHaveBeenSentError(DomainError):
    """The provider never said whether the code went out, so it is counted as if it did.

    Carries when another code may be asked for: the same wait a code that did go out imposes.
    """

    failure_kind = FailureKind.UNAVAILABLE

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("the code may have been sent; try again shortly")
        self.retry_after_seconds = retry_after_seconds


# How long the deployment's budget refuses codes once it is spent.
BUDGET_RETRY_AFTER_SECONDS: Final = int(timedelta(minutes=10).total_seconds())

# How long to wait before offering the same code again when its provider could not check it. Short,
# because the challenge expires in minutes; not zero, so a client does not hammer a failing one.
CHECK_RETRY_AFTER_SECONDS: Final = 5


class CodeNotCheckedError(DomainError):
    """The provider that holds the code could not say whether it was right.

    Neither a right code nor a wrong one: no attempt is counted and nobody is signed in. Carries
    when to try the same code again, since the challenge is still open.
    """

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
    """The numbers that govern signing in.

    Configuration rather than constants in the code, so that a deployment under attack can be
    tightened without a release. The defaults are what a small deployment can live with and an
    attacker cannot profit from; each is explained where it is applied.
    """

    # Sliding: every renewal starts it again, so somebody who opens the app within this long of the
    # last time is never asked for their number again.
    refresh_token_lifetime: timedelta = timedelta(days=90)

    # How long one number waits before another code, by how many it has had today: the first
    # resend is quick, because codes do go astray, and each after that waits longer.
    resend_cooldowns: tuple[timedelta, ...] = (
        timedelta(seconds=30),
        timedelta(seconds=60),
        timedelta(minutes=2),
        timedelta(minutes=5),
    )
    challenges_per_number: int = 5
    challenges_per_number_window: timedelta = timedelta(hours=1)
    challenges_per_number_per_day: int = 10

    # Wrong codes one number may have, across every code sent to it, before signing in to it
    # waits. Five tries per code times ten codes a day is still one chance in twenty thousand.
    failed_codes_per_number: int = 10
    failed_codes_window: timedelta = timedelta(hours=24)

    challenges_per_source: int = 20
    challenges_per_source_window: timedelta = timedelta(hours=1)
    verifications_per_source: int = 60
    verifications_per_source_window: timedelta = timedelta(hours=1)

    # Calling codes a code may be sent to, without the plus. `None` sends anywhere, which is what a
    # development deployment wants and what a production one should not.
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
    """What the caller gets back when a code has been sent.

    The identifier and nothing else. In particular not the code, not whether the number already
    has an account, and not how many attempts remain before one was made.
    """

    challenge_id: str
    expires_in_seconds: int
    # When this number may be sent another code, so an app can count down rather than guess.
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
        # Two hashers, and the difference matters. A one-time code is *verified* against one
        # known row, so it is salted and deliberately slow. A refresh token has to be *found*,
        # so it is hashed deterministically with a server-held key — a salted hash would make
        # the lookup a scan of every row, and using one here means no token is ever found at
        # all, which is a bug that unit tests with a single hasher cannot see.
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
        """Send a code to the number, once every layer in the module's docstring allows it.

        The per-source limit is counted by the limiter, because it is about bursts from one place
        and an approximate answer within a window is enough. Everything about a number or the
        deployment is counted in the repository, so it survives a restart and cannot be reset by
        an attacker waiting for a deployment.
        """
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

        # None where the provider makes the code: then nothing about it is known here to hash.
        code = None if self._otp.issues_its_own_codes(number) else self._challenge_code()
        challenge = OTPChallenge(
            id=self._ids.generate(),
            phone_number=number,
            code_hash=None if code is None else self._code_hasher.hash(code),
            issued_at=now,
            expires_at=now + CHALLENGE_LIFETIME,
        )
        # Before the new one is stored: only the latest code works.
        await self._challenges.supersede_open(number, now)
        await self._challenges.add(challenge)

        # Sent after the challenge is stored. The other order can deliver a code that nothing
        # will accept, which looks to the user exactly like the product being broken.
        try:
            if code is None:
                await self._otp.send_own_code(number)
            else:
                await self._otp.send(number, code)
        except DeliveryUncertainError as error:
            # Kept, and counted: a slow provider may have delivered it, and an uncounted delivery
            # is a way past the cooldown and the budgets while the bill still arrives.
            raise CodeMayHaveBeenSentError(self._wait_for_another([*issued, now], now)) from error
        if self._metrics is not None:
            self._metrics.increment(CHALLENGE_SENT)

        return ChallengeIssued(
            challenge_id=challenge.id,
            expires_in_seconds=int(CHALLENGE_LIFETIME.total_seconds()),
            resend_after_seconds=self._wait_for_another([*issued, now], now),
        )

    def _wait_for_another(self, issued: list[datetime], now: datetime) -> int:
        """Seconds until this number may be sent another code, given when it was sent each today.

        Zero when it may be sent one now. The longest of three answers: the cooldown after the
        last code, the hourly limit and the daily limit, each saying when its oldest counted code
        leaves its window.
        """
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
            # The whole window: an exact release time would say how the failures are spread.
            raise RateLimitedError(int(policy.failed_codes_window.total_seconds()))

    async def _refuse_over_budget(self, number: PhoneNumber, now: datetime) -> None:
        """The deployment has sent as many codes this hour as it will, overall or to this country.

        Deliberately a circuit breaker that also stops genuine sign-ins, because the alternative
        is a bill. The metric it records is the alarm somebody should be woken by.
        """
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
        """The code for a new challenge: random, unless a testing provider fixes it.

        A fixed code is accepted only from a provider that admits it is not safe for production,
        which is the same provider the application refuses to start with there. Anything else
        offering one is a mistake that would give every account the same code, so it is refused
        loudly rather than used.
        """
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
        """Exchange a correct code for a session, creating the account if there is not one.

        Limited per source, so one place cannot guess at many numbers' codes at once, and per
        number across every code it has been sent, so asking for a new code does not reset the
        guesses. A locked number refuses even the right code: accepting it would make the lock a
        suggestion to guess more slowly.
        """
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
            # The attempt is consumed on failure, or the limit is advisory.
            await self._challenges.update(challenge.with_failed_attempt())
            raise AuthenticationError("that code is not valid")

        await self._challenges.update(challenge.verified(now))

        user = await self._users.find_by_number(challenge.phone_number)
        if user is None:
            user = User(id=UserId(self._ids.generate()), phone_number=challenge.phone_number)
            await self._users.add(user)

        return await self._issue_pair(user.id, family_id=self._ids.generate(), now=now)

    async def _is_the_code(self, challenge: OTPChallenge, code: str) -> bool:
        """Whether `code` is the challenge's: by its hash, or by asking the provider that made it.

        Asked while the challenge's row is held, as the hash is compared, so two guesses at one
        challenge are still counted one after the other. A provider that could not answer is
        `CodeNotCheckedError`, never a yes: the caller counts no attempt and signs nobody in.
        """
        if challenge.code_hash is not None:
            return self._code_hasher.verify(code, challenge.code_hash)
        number = challenge.phone_number
        if not self._otp.issues_its_own_codes(number):
            # The provider serving this number changed since the code was sent, and none that is
            # configured now can check it. The challenge can never be satisfied.
            return False
        try:
            return await self._otp.check(number, code)
        except ProviderError as error:
            raise CodeNotCheckedError(error.provider, error.reason) from error

    # -------------------------------------------------------------- refreshing

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Exchange a refresh token for a new pair, retiring the old one.

        A token that has already been exchanged means a copy exists somewhere it should not.
        There is no way to tell whether the copy is the user's or a thief's, so the whole family
        is revoked: the legitimate user signs in again, and the thief gets nothing.
        """
        now = self._clock.now()
        stored = await self._refresh_tokens.find_by_hash(self._token_hasher.hash(refresh_token))

        if stored is None:
            raise AuthenticationError("that session is not valid")

        if stored.was_already_used:
            if stored.is_within_reuse_leeway_at(now):
                # The phone asked, the answer never reached its keychain, and it asked again. A new
                # pair, and nothing revoked: see REFRESH_REUSE_LEEWAY.
                return await self._issue_pair(stored.user_id, family_id=stored.family_id, now=now)
            await self._refresh_tokens.revoke_family(stored.family_id, now)
            raise AuthenticationError("that session is not valid")

        if not stored.is_usable_at(now):
            raise AuthenticationError("that session is not valid")

        await self._refresh_tokens.update(stored.rotated(now))
        return await self._issue_pair(stored.user_id, family_id=stored.family_id, now=now)

    # --------------------------------------------------------------- ending it

    async def sign_out(self, refresh_token: str) -> UserId | None:
        """End this session, returning whose it was, or nothing for a token nobody holds.

        Silent to the client when the token is unknown. A caller signing out has nothing to gain
        from being told their token was already invalid, and saying so tells an attacker whether
        a token they hold is real. The owner is returned for the server's own use: the device
        signing out stops receiving that account's notifications.
        """
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
    """Delete the challenges nothing can use or count any more, returning how many went.

    Each holds the number a code was sent to, for anybody who typed one in, account or not. One
    that has expired can never be verified, but it is still counted against its number until the
    longest window that reads it has passed it — the daily limits and the lock on wrong codes — so
    that is when it goes: sooner would hand a number its limits back as each code expired.
    """
    window = (policy or AuthenticationPolicy()).retention
    return await challenges.delete_expired(clock.now() - window)
