"""Signing in.

The use case, composed from ports. It contains no SQL, no HTTP and no vendor, and every
decision it makes is one the domain model already knows how to express.

Two properties are worth stating plainly because they are easy to lose in a refactor:

  A failure never says which part was wrong. "No such account" and "wrong code" are the same
  response, because the difference tells an attacker which numbers are worth attacking.

  Verifying a code consumes an attempt whether or not the code was right, and whether or not
  the challenge existed. Otherwise the attempt limit is advisory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from letmehandle.domain.errors import DomainError, InvariantError
from letmehandle.domain.models.auth import (
    CHALLENGE_LIFETIME,
    CODE_LENGTH,
    OTPChallenge,
    RefreshToken,
    TokenPair,
)
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.user import User

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.clock import Clock, IdGenerator
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


class RateLimitedError(AuthenticationError):
    """Too many attempts. Carries when to try again, so a client does not simply retry."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many attempts; try again shortly")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class AuthenticationPolicy:
    """The numbers that govern signing in.

    Configuration rather than constants in the code, so that a deployment under attack can be
    tightened without a release.
    """

    refresh_token_lifetime: timedelta = timedelta(days=30)
    challenges_per_number: int = 5
    challenges_per_number_window: timedelta = timedelta(hours=1)
    challenges_per_source: int = 20
    challenges_per_source_window: timedelta = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class ChallengeIssued:
    """What the caller gets back when a code has been sent.

    The identifier and nothing else. In particular not the code, not whether the number already
    has an account, and not how many attempts remain before one was made.
    """

    challenge_id: str
    expires_in_seconds: int


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

    # ------------------------------------------------------------- requesting

    async def request_challenge(
        self, number: PhoneNumber, *, source: str | None = None
    ) -> ChallengeIssued:
        """Send a code to the number.

        Two limits, deliberately measured differently. The per-number one is counted in the
        repository, so it survives a restart and cannot be reset by an attacker waiting for a
        deployment. The per-source one is counted by the limiter, because it is about bursts
        from one place and an approximate answer within a window is enough.
        """
        now = self._clock.now()

        if source is not None:
            decision = await self._rate_limiter.check(
                f"challenge:source:{source}",
                limit=self._policy.challenges_per_source,
                window=self._policy.challenges_per_source_window,
            )
            if not decision.allowed:
                raise RateLimitedError(decision.retry_after_seconds)

        issued_recently = await self._challenges.count_issued_since(
            number, now - self._policy.challenges_per_number_window
        )
        if issued_recently >= self._policy.challenges_per_number:
            raise RateLimitedError(int(self._policy.challenges_per_number_window.total_seconds()))

        code = self._challenge_code()
        challenge = OTPChallenge(
            id=self._ids.generate(),
            phone_number=number,
            code_hash=self._code_hasher.hash(code),
            issued_at=now,
            expires_at=now + CHALLENGE_LIFETIME,
        )
        await self._challenges.add(challenge)

        # Sent after the challenge is stored. The other order can deliver a code that nothing
        # will accept, which looks to the user exactly like the product being broken.
        await self._otp.send(number, code)

        return ChallengeIssued(
            challenge_id=challenge.id,
            expires_in_seconds=int(CHALLENGE_LIFETIME.total_seconds()),
        )

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

    async def verify(self, challenge_id: str, code: str) -> TokenPair:
        """Exchange a correct code for a session, creating the account if there is not one."""
        now = self._clock.now()
        challenge = await self._challenges.get(challenge_id)

        if challenge is None or not challenge.is_open_at(now):
            raise AuthenticationError("that code is not valid")

        if not self._code_hasher.verify(code, challenge.code_hash):
            # The attempt is consumed on failure, or the limit is advisory.
            await self._challenges.update(challenge.with_failed_attempt())
            raise AuthenticationError("that code is not valid")

        await self._challenges.update(challenge.verified(now))

        user = await self._users.find_by_number(challenge.phone_number)
        if user is None:
            user = User(id=UserId(self._ids.generate()), phone_number=challenge.phone_number)
            await self._users.add(user)

        return await self._issue_pair(user.id, family_id=self._ids.generate(), now=now)

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

    async def sign_out_everywhere(self, user_id: UserId) -> int:
        """End every session this user has, returning how many were ended."""
        return await self._refresh_tokens.revoke_all_for_user(user_id, self._clock.now())

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
    per-number window has passed it, so that is when it goes: sooner would hand a number its limit
    back as each code expired.
    """
    window = (policy or AuthenticationPolicy()).challenges_per_number_window
    return await challenges.delete_expired(clock.now() - window)
