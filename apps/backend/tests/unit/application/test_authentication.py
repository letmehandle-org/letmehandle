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
    UnservedNumberError,
    forget_spent_challenges,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.auth import CHALLENGE_LIFETIME, MAX_ATTEMPTS, REFRESH_REUSE_LEEWAY
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
from tests.support.recording_metrics import RecordingMetrics

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")
ANOTHER_NUMBER = PhoneNumber.parse("+12025550144")
THE_CODE = "424242"


class Harness:
    """Everything the service needs, assembled and reachable from a test."""

    def __init__(
        self,
        *,
        limiter: object | None = None,
        otp: RecordingOTPProvider | None = None,
        policy: AuthenticationPolicy | None = None,
    ) -> None:
        self.users = InMemoryUserRepository()
        self.challenges = InMemoryChallengeRepository()
        self.refresh_tokens = InMemoryRefreshTokenRepository()
        self.otp = otp or RecordingOTPProvider()
        self.clock = FixedClock(NOW)
        self.ids = CountingIdGenerator()
        self.signer = FakeTokenSigner()
        self.limiter = limiter or NeverLimits()
        self.metrics = RecordingMetrics()
        # Without a resend cooldown unless the test is about one: signing the same number in twice
        # is ordinary here, and the cooldown has tests of its own.
        self.policy = policy or AuthenticationPolicy(resend_cooldowns=(timedelta(0),))
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
            policy=self.policy,
            metrics=self.metrics,
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
            policy=AuthenticationPolicy(challenges_per_number=2, resend_cooldowns=(timedelta(0),)),
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
            policy=AuthenticationPolicy(challenges_per_number=1, resend_cooldowns=(timedelta(0),)),
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
        harness.clock.advance((REFRESH_REUSE_LEEWAY + timedelta(seconds=1)).total_seconds())

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

    async def test_reusing_a_rotated_token_revokes_the_whole_family(self, harness: Harness) -> None:
        # The property that turns a stolen refresh token from indefinite access into one use
        # and an alarm. Either copy could be the thief's, and nothing can tell which, so both
        # stop working and the legitimate user signs in again.
        original = await harness.sign_in()
        successor = await harness.service.refresh(original)
        harness.clock.advance((REFRESH_REUSE_LEEWAY + timedelta(seconds=1)).total_seconds())

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(successor.refresh_token)

    async def test_an_unknown_token_is_refused(self, harness: Harness) -> None:
        with pytest.raises(AuthenticationError):
            await harness.service.refresh("not-a-token")

    async def test_an_expired_token_is_refused(self, harness: Harness) -> None:
        original = await harness.sign_in()
        harness.clock.advance(timedelta(days=91).total_seconds())

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


class FixedCodeOTPProvider(RecordingOTPProvider):
    """A testing provider that fixes the code, optionally claiming to be safe for production."""

    def __init__(self, code: str, *, production_safe: bool = False) -> None:
        super().__init__()
        self._code = code
        self._production_safe = production_safe

    @property
    def is_safe_for_production(self) -> bool:
        return self._production_safe

    @property
    def fixed_code(self) -> str:
        return self._code


class TestAFixedTestingCode:
    async def test_a_testing_provider_fixes_the_code_it_sends(self) -> None:
        harness = Harness(otp=FixedCodeOTPProvider("123456"))
        issued = await harness.service.request_challenge(NUMBER)
        assert harness.otp.sent == [(NUMBER, "123456")]
        pair = await harness.service.verify(issued.challenge_id, "123456")
        assert pair.refresh_token

    async def test_a_provider_without_one_still_sends_a_random_code(self, harness: Harness) -> None:
        await harness.service.request_challenge(NUMBER)
        assert harness.otp.sent == [(NUMBER, THE_CODE)]

    async def test_a_real_provider_may_not_fix_the_code(self) -> None:
        # Every account would share one code, and nothing about the service would look wrong.
        harness = Harness(otp=FixedCodeOTPProvider("123456", production_safe=True))
        with pytest.raises(InvariantError, match="must not fix them"):
            await harness.service.request_challenge(NUMBER)
        assert harness.otp.sent == []

    async def test_a_fixed_code_must_be_a_real_code(self) -> None:
        harness = Harness(otp=FixedCodeOTPProvider("12ab"))
        with pytest.raises(InvariantError, match="6 digits"):
            await harness.service.request_challenge(NUMBER)


class TestStayingSignedIn:
    async def test_a_rotated_token_presented_again_within_the_leeway_gets_a_new_pair(
        self, harness: Harness
    ) -> None:
        # The app was killed between the server rotating the token and the keychain keeping the
        # new one. Coming back with the old one must not cost the user their session.
        original = await harness.sign_in()
        lost = await harness.service.refresh(original)
        harness.clock.advance(REFRESH_REUSE_LEEWAY.total_seconds())

        recovered = await harness.service.refresh(original)

        assert recovered.refresh_token not in {original, lost.refresh_token}
        assert await harness.service.refresh(recovered.refresh_token)
        assert await harness.service.refresh(lost.refresh_token)

    async def test_a_revoked_token_gets_no_leeway(self, harness: Harness) -> None:
        original = await harness.sign_in()
        await harness.service.refresh(original)
        await harness.service.sign_out(original)

        with pytest.raises(AuthenticationError):
            await harness.service.refresh(original)

    async def test_renewing_starts_the_ninety_days_again(self, harness: Harness) -> None:
        token = await harness.sign_in()
        for _ in range(4):
            harness.clock.advance(timedelta(days=80).total_seconds())
            token = (await harness.service.refresh(token)).refresh_token
        # Three hundred and twenty days after signing in, still signed in.
        assert token


def strict(**changes: object) -> Harness:
    """A harness with the production defaults, changed only where the test says."""
    return Harness(policy=AuthenticationPolicy(**changes))  # type: ignore[arg-type]


class TestSendingCodesToOneNumber:
    async def test_each_resend_waits_longer(self) -> None:
        harness = strict()
        waits = []
        for expected in (30, 60, 120, 300):
            issued = await harness.service.request_challenge(NUMBER)
            waits.append(issued.resend_after_seconds)
            with pytest.raises(RateLimitedError) as too_soon:
                await harness.service.request_challenge(NUMBER)
            assert too_soon.value.retry_after_seconds == expected
            harness.clock.advance(expected)
        assert waits == [30, 60, 120, 300]

    async def test_the_hourly_limit_says_when_its_oldest_code_leaves_the_window(self) -> None:
        harness = strict(challenges_per_number=2, resend_cooldowns=(timedelta(seconds=10),))
        await harness.service.request_challenge(NUMBER)
        harness.clock.advance(600)
        second = await harness.service.request_challenge(NUMBER)
        assert second.resend_after_seconds == 3000

        harness.clock.advance(60)
        with pytest.raises(RateLimitedError) as refused:
            await harness.service.request_challenge(NUMBER)
        assert refused.value.retry_after_seconds == 2940

    async def test_the_daily_limit_holds_once_the_hours_have_passed(self) -> None:
        harness = strict(
            challenges_per_number=100,
            challenges_per_number_per_day=3,
            resend_cooldowns=(timedelta(0),),
        )
        for _ in range(3):
            await harness.service.request_challenge(NUMBER)
            harness.clock.advance(timedelta(hours=2).total_seconds())

        with pytest.raises(RateLimitedError) as refused:
            await harness.service.request_challenge(NUMBER)
        assert refused.value.retry_after_seconds == timedelta(hours=18).total_seconds()

    async def test_only_the_latest_code_works(self, harness: Harness) -> None:
        old = await harness.service.request_challenge(NUMBER)
        new = await harness.service.request_challenge(NUMBER)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(old.challenge_id, THE_CODE)
        assert await harness.service.verify(new.challenge_id, THE_CODE)


class TestGuessing:
    async def test_wrong_codes_across_new_codes_lock_the_number(self) -> None:
        harness = Harness(
            policy=AuthenticationPolicy(failed_codes_per_number=6, resend_cooldowns=(timedelta(0),))
        )
        for _ in range(2):
            issued = await harness.service.request_challenge(NUMBER)
            for _ in range(3):
                with pytest.raises(AuthenticationError):
                    await harness.service.verify(issued.challenge_id, "000000")

        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(NUMBER)
        assert ("auth.challenge.refused", {"outcome": "locked"}) in [
            (count.name, dict(count.labels)) for count in harness.metrics.counts
        ]

    async def test_a_locked_number_refuses_even_the_right_code(self) -> None:
        harness = Harness(
            policy=AuthenticationPolicy(failed_codes_per_number=2, resend_cooldowns=(timedelta(0),))
        )
        issued = await harness.service.request_challenge(NUMBER)
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                await harness.service.verify(issued.challenge_id, "000000")

        with pytest.raises(RateLimitedError):
            await harness.service.verify(issued.challenge_id, THE_CODE)

    async def test_the_lock_lifts_when_the_window_passes(self) -> None:
        harness = Harness(
            policy=AuthenticationPolicy(failed_codes_per_number=1, resend_cooldowns=(timedelta(0),))
        )
        issued = await harness.service.request_challenge(NUMBER)
        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, "000000")
        harness.clock.advance(timedelta(hours=24, seconds=1).total_seconds())

        assert await harness.service.request_challenge(NUMBER)

    async def test_one_place_guessing_at_many_codes_is_limited(self) -> None:
        limiter = CountingRateLimiter(NOW)
        harness = Harness(
            limiter=limiter,
            policy=AuthenticationPolicy(
                verifications_per_source=1, resend_cooldowns=(timedelta(0),)
            ),
        )
        issued = await harness.service.request_challenge(NUMBER)
        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, "000000", source="one-place")

        with pytest.raises(RateLimitedError):
            await harness.service.verify(issued.challenge_id, THE_CODE, source="one-place")
        assert await harness.service.verify(issued.challenge_id, THE_CODE, source="elsewhere")


class TestTheDeploymentsBudget:
    async def test_a_country_that_is_not_served_is_never_sent_a_code(self) -> None:
        harness = strict(allowed_calling_codes=frozenset({"44", "91"}))

        with pytest.raises(UnservedNumberError):
            await harness.service.request_challenge(NUMBER)
        assert harness.otp.sent == []
        assert await harness.service.request_challenge(PhoneNumber.parse("+447700900123"))

    async def test_the_hourly_budget_stops_sending_to_anybody(self) -> None:
        harness = strict(challenges_per_hour=2)
        await harness.service.request_challenge(NUMBER)
        await harness.service.request_challenge(ANOTHER_NUMBER)

        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(PhoneNumber.parse("+12025550145"))
        harness.clock.advance(timedelta(hours=1, seconds=1).total_seconds())
        assert await harness.service.request_challenge(PhoneNumber.parse("+12025550145"))

    async def test_one_country_s_budget_leaves_the_others_alone(self) -> None:
        harness = strict(challenges_per_hour_per_calling_code=1)
        await harness.service.request_challenge(NUMBER)

        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(ANOTHER_NUMBER)
        assert await harness.service.request_challenge(PhoneNumber.parse("+447700900123"))
        assert ("auth.challenge.refused", {"outcome": "country_budget"}) in [
            (count.name, dict(count.labels)) for count in harness.metrics.counts
        ]

    async def test_unbounded_budgets_are_not_counted(self) -> None:
        harness = strict(challenges_per_hour=None, challenges_per_hour_per_calling_code=None)
        assert await harness.service.request_challenge(NUMBER)
        assert [count.name for count in harness.metrics.counts] == ["auth.challenge.sent"]

    async def test_spent_codes_are_kept_as_long_as_the_longest_limit_reads_them(self) -> None:
        harness = strict()
        await harness.service.request_challenge(NUMBER)
        harness.clock.advance(timedelta(hours=23).total_seconds())
        assert await forget_spent_challenges(harness.challenges, harness.clock) == 0
        harness.clock.advance(timedelta(hours=2).total_seconds())
        assert await forget_spent_challenges(harness.challenges, harness.clock) == 1
