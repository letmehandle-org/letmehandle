"""Signing in, exercised against storage that really stores.

Every test here describes an attack or a mistake somebody will actually make: a replayed code,
a stolen refresh token, an enumerated phone number, a brute-forced six digits.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.application.auth.service import (
    AuthenticationError,
    AuthenticationPolicy,
    AuthenticationService,
    RateLimitedError,
)
from letmehandle.domain.models.auth import CHALLENGE_LIFETIME, MAX_ATTEMPTS
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.contracts.auth_fakes import (
    CountingRateLimiter,
    FakeTokenSigner,
    InMemoryChallengeRepository,
    InMemoryRefreshTokenRepository,
    InMemoryUserRepository,
    NeverLimits,
    PredictableSecretGenerator,
    SaltedHasher,
    Sha256Hasher,
)
from tests.contracts.fakes import CountingIdGenerator, FixedClock, RecordingOTPProvider

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")
ANOTHER_NUMBER = PhoneNumber.parse("+12025550144")
THE_CODE = "424242"


class Harness:
    """Everything the service needs, assembled and reachable from a test."""

    def __init__(self, *, limiter: object | None = None) -> None:
        self.users = InMemoryUserRepository()
        self.challenges = InMemoryChallengeRepository()
        self.refresh_tokens = InMemoryRefreshTokenRepository()
        self.otp = RecordingOTPProvider()
        self.clock = FixedClock(NOW)
        self.ids = CountingIdGenerator()
        self.signer = FakeTokenSigner()
        self.limiter = limiter or NeverLimits()
        self.service = AuthenticationService(
            users=self.users,
            challenges=self.challenges,
            refresh_tokens=self.refresh_tokens,
            otp=self.otp,
            code_hasher=Sha256Hasher(),
            token_hasher=Sha256Hasher(),
            secrets=PredictableSecretGenerator(THE_CODE),
            signer=self.signer,
            clock=self.clock,
            ids=self.ids,
            rate_limiter=self.limiter,  # type: ignore[arg-type]
        )

    async def sign_in(self, number: PhoneNumber = NUMBER) -> str:
        issued = await self.service.request_challenge(number)
        pair = await self.service.verify(issued.challenge_id, THE_CODE)
        return pair.refresh_token


@pytest.fixture
def harness() -> Harness:
    return Harness()


class TestRequestingACode:
    async def test_a_code_is_sent_to_the_number(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        assert issued.challenge_id
        assert issued.expires_in_seconds == int(CHALLENGE_LIFETIME.total_seconds())
        assert harness.otp.sent == [(NUMBER, THE_CODE)]

    async def test_the_code_is_stored_only_as_a_hash(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        stored = await harness.challenges.get(issued.challenge_id)
        assert stored is not None
        assert THE_CODE not in stored.code_hash

    async def test_the_challenge_exists_before_the_code_is_sent(self, harness: Harness) -> None:
        # The other order can deliver a code that nothing will accept, which looks to the user
        # exactly like the product being broken.
        issued = await harness.service.request_challenge(NUMBER)
        assert await harness.challenges.get(issued.challenge_id) is not None

    async def test_requesting_says_nothing_about_whether_an_account_exists(
        self, harness: Harness
    ) -> None:
        # Enumeration is the expensive half of attacking a phone-number identity, and this is
        # where it would be made cheap.
        await harness.sign_in(NUMBER)
        for_existing = await harness.service.request_challenge(NUMBER)
        for_new = await harness.service.request_challenge(ANOTHER_NUMBER)
        assert for_existing.expires_in_seconds == for_new.expires_in_seconds

    async def test_the_per_number_limit_holds(self) -> None:
        harness = Harness()
        harness.service = AuthenticationService(
            users=harness.users,
            challenges=harness.challenges,
            refresh_tokens=harness.refresh_tokens,
            otp=harness.otp,
            code_hasher=Sha256Hasher(),
            token_hasher=Sha256Hasher(),
            secrets=PredictableSecretGenerator(THE_CODE),
            signer=harness.signer,
            clock=harness.clock,
            ids=harness.ids,
            rate_limiter=NeverLimits(),
            policy=AuthenticationPolicy(challenges_per_number=2),
        )
        await harness.service.request_challenge(NUMBER)
        await harness.service.request_challenge(NUMBER)

        with pytest.raises(RateLimitedError) as failure:
            await harness.service.request_challenge(NUMBER)
        # Told when to come back, or a client simply retries and makes it worse.
        assert failure.value.retry_after_seconds > 0

    async def test_the_per_number_limit_is_per_number(self, harness: Harness) -> None:
        harness.service = AuthenticationService(
            users=harness.users,
            challenges=harness.challenges,
            refresh_tokens=harness.refresh_tokens,
            otp=harness.otp,
            code_hasher=Sha256Hasher(),
            token_hasher=Sha256Hasher(),
            secrets=PredictableSecretGenerator(THE_CODE),
            signer=harness.signer,
            clock=harness.clock,
            ids=harness.ids,
            rate_limiter=NeverLimits(),
            policy=AuthenticationPolicy(challenges_per_number=1),
        )
        await harness.service.request_challenge(NUMBER)
        await harness.service.request_challenge(ANOTHER_NUMBER)

    async def test_the_per_source_limit_holds(self) -> None:
        limiter = CountingRateLimiter(NOW)
        harness = Harness(limiter=limiter)
        harness.service = AuthenticationService(
            users=harness.users,
            challenges=harness.challenges,
            refresh_tokens=harness.refresh_tokens,
            otp=harness.otp,
            code_hasher=Sha256Hasher(),
            token_hasher=Sha256Hasher(),
            secrets=PredictableSecretGenerator(THE_CODE),
            signer=harness.signer,
            clock=harness.clock,
            ids=harness.ids,
            rate_limiter=limiter,
            policy=AuthenticationPolicy(challenges_per_source=2),
        )
        # Different numbers, one source: the shape of an attacker enumerating numbers, which
        # the per-number limit cannot see.
        await harness.service.request_challenge(NUMBER, source="one-place")
        await harness.service.request_challenge(ANOTHER_NUMBER, source="one-place")

        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(
                PhoneNumber.parse("+12025550145"), source="one-place"
            )


class TestVerifying:
    async def test_a_correct_code_creates_an_account_and_signs_in(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        pair = await harness.service.verify(issued.challenge_id, THE_CODE)

        assert pair.access_token
        assert pair.refresh_token
        assert await harness.users.find_by_number(NUMBER) is not None

    async def test_signing_in_again_reuses_the_same_account(self, harness: Harness) -> None:
        await harness.sign_in()
        first = await harness.users.find_by_number(NUMBER)

        await harness.sign_in()
        second = await harness.users.find_by_number(NUMBER)

        assert first is not None and second is not None
        assert first.id == second.id
        assert len(harness.users.by_id) == 1

    async def test_a_wrong_code_is_refused_and_consumes_an_attempt(self, harness: Harness) -> None:
        # Without consuming the attempt the limit is advisory, and six digits is nothing.
        issued = await harness.service.request_challenge(NUMBER)
        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, "000000")

        stored = await harness.challenges.get(issued.challenge_id)
        assert stored is not None
        assert stored.attempts == 1

    async def test_the_attempts_run_out(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(AuthenticationError):
                await harness.service.verify(issued.challenge_id, "000000")

        # Even the right code is refused once the attempts are gone.
        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, THE_CODE)

    async def test_a_code_cannot_be_used_twice(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        await harness.service.verify(issued.challenge_id, THE_CODE)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, THE_CODE)

    async def test_an_expired_code_is_refused(self, harness: Harness) -> None:
        issued = await harness.service.request_challenge(NUMBER)
        harness.clock.advance(CHALLENGE_LIFETIME.total_seconds() + 1)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, THE_CODE)

    async def test_an_unknown_challenge_is_refused_the_same_way(self, harness: Harness) -> None:
        # Identical failure to a wrong code. Distinguishing them tells an attacker whether a
        # challenge identifier they hold is real.
        with pytest.raises(AuthenticationError):
            await harness.service.verify("no-such-challenge", THE_CODE)


class TestHashing:
    async def test_a_refresh_token_is_found_even_when_codes_are_salted(self) -> None:
        """The bug this exists to prevent: one hasher used for both jobs.

        A one-time code is verified against a known row, so it is salted. A refresh token has
        to be found by its hash, which a salted hash makes impossible — every lookup misses,
        and every renewal fails with the same message as a stolen token. A suite that uses one
        deterministic hasher for both cannot see it.
        """
        harness = Harness()
        harness.service = AuthenticationService(
            users=harness.users,
            challenges=harness.challenges,
            refresh_tokens=harness.refresh_tokens,
            otp=harness.otp,
            code_hasher=SaltedHasher(),
            token_hasher=Sha256Hasher(),
            secrets=PredictableSecretGenerator(THE_CODE),
            signer=harness.signer,
            clock=harness.clock,
            ids=harness.ids,
            rate_limiter=NeverLimits(),
        )

        issued = await harness.service.request_challenge(NUMBER)
        pair = await harness.service.verify(issued.challenge_id, THE_CODE)

        renewed = await harness.service.refresh(pair.refresh_token)
        assert renewed.refresh_token != pair.refresh_token


class TestRefreshing:
    async def test_a_refresh_returns_a_new_pair(self, harness: Harness) -> None:
        original = await harness.sign_in()
        pair = await harness.service.refresh(original)
        assert pair.refresh_token != original

    async def test_the_old_token_stops_working(self, harness: Harness) -> None:
        original = await harness.sign_in()
        await harness.service.refresh(original)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

    async def test_reusing_a_rotated_token_revokes_the_whole_family(self, harness: Harness) -> None:
        # The property that turns a stolen refresh token from indefinite access into one use
        # and an alarm. Either copy could be the thief's, and nothing can tell which, so both
        # stop working and the legitimate user signs in again.
        original = await harness.sign_in()
        successor = await harness.service.refresh(original)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(successor.refresh_token)

    async def test_an_unknown_token_is_refused(self, harness: Harness) -> None:
        with pytest.raises(AuthenticationError):
            await harness.service.refresh("not-a-token")

    async def test_an_expired_token_is_refused(self, harness: Harness) -> None:
        original = await harness.sign_in()
        harness.clock.advance(timedelta(days=31).total_seconds())

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

    async def test_two_sign_ins_are_independent_families(self, harness: Harness) -> None:
        # Signing out on one device must not sign the user out on another.
        first = await harness.sign_in()
        second = await harness.sign_in()

        await harness.service.sign_out(first)
        assert await harness.service.refresh(second)


class TestSigningOut:
    async def test_it_invalidates_the_session(self, harness: Harness) -> None:
        token = await harness.sign_in()
        await harness.service.sign_out(token)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(token)

    async def test_it_is_silent_about_a_token_it_does_not_know(self, harness: Harness) -> None:
        # Saying so would tell an attacker whether a token they hold is real.
        await harness.service.sign_out("not-a-token")

    async def test_signing_out_everywhere_ends_every_session(self, harness: Harness) -> None:
        first = await harness.sign_in()
        second = await harness.sign_in()
        user = await harness.users.find_by_number(NUMBER)
        assert user is not None

        ended = await harness.service.sign_out_everywhere(user.id)

        assert ended == 2
        for token in (first, second):
            with pytest.raises(AuthenticationError):
                await harness.service.refresh(token)
