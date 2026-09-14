"""Signing in where the provider holds the code, with every limit still the service's (D-042)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from letmehandle.application.auth.service import (
    CHECK_RETRY_AFTER_SECONDS,
    AuthenticationError,
    AuthenticationPolicy,
    CodeMayHaveBeenSentError,
    CodeNotCheckedError,
    RateLimitedError,
    UnservedNumberError,
)
from letmehandle.domain.errors import (
    DeliveryUncertainError,
    ProviderError,
    UnreachableNumberError,
)
from letmehandle.domain.models.auth import CHALLENGE_LIFETIME, MAX_ATTEMPTS
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.contracts.auth_fakes import CountingRateLimiter
from tests.contracts.fakes import CheckingOTPProvider, RecordingOTPProvider
from tests.unit.application.test_authentication import NOW, NUMBER, THE_CODE, Harness

WRONG = "000000"


def holding(**changes: object) -> tuple[Harness, CheckingOTPProvider]:
    """A service whose provider makes its codes, with the policy changed as given."""
    provider = CheckingOTPProvider()
    policy = AuthenticationPolicy(**{"resend_cooldowns": (timedelta(0),), **changes})  # type: ignore[arg-type]
    return Harness(otp=provider, policy=policy), provider


async def stored_attempts(harness: Harness, challenge_id: str) -> int:
    stored = await harness.challenges.get(challenge_id)
    assert stored is not None
    return stored.attempts


class TestSending:
    async def test_the_provider_is_asked_for_a_code_and_handed_none(self) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)

        assert provider.issued == [NUMBER]
        assert provider.sent == []
        stored = await harness.challenges.get(issued.challenge_id)
        assert stored is not None
        assert stored.code_is_held_by_provider
        assert issued.expires_in_seconds == int(CHALLENGE_LIFETIME.total_seconds())

    async def test_a_country_that_is_not_served_is_never_sent_a_code(self) -> None:
        harness, provider = holding(allowed_calling_codes=frozenset({"44"}))
        with pytest.raises(UnservedNumberError):
            await harness.service.request_challenge(NUMBER)
        assert provider.issued == []

    async def test_the_resend_wait_and_the_hourly_limit_still_hold(self) -> None:
        harness, provider = holding(
            resend_cooldowns=(timedelta(seconds=30),), challenges_per_number=2
        )
        await harness.service.request_challenge(NUMBER)
        with pytest.raises(RateLimitedError) as too_soon:
            await harness.service.request_challenge(NUMBER)
        assert too_soon.value.retry_after_seconds == 30

        harness.clock.advance(30)
        await harness.service.request_challenge(NUMBER)
        harness.clock.advance(30)
        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(NUMBER)
        assert provider.issued == [NUMBER, NUMBER]

    async def test_the_per_source_and_the_deployments_budgets_still_hold(self) -> None:
        limiter = CountingRateLimiter(NOW)
        provider = CheckingOTPProvider()
        harness = Harness(
            limiter=limiter,
            otp=provider,
            policy=AuthenticationPolicy(
                challenges_per_source=2,
                challenges_per_hour_per_calling_code=1,
                resend_cooldowns=(timedelta(0),),
            ),
        )
        await harness.service.request_challenge(NUMBER, source="one-place")
        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(PhoneNumber.parse("+12025550144"))
        await harness.service.request_challenge(
            PhoneNumber.parse("+447700900123"), source="one-place"
        )
        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(
                PhoneNumber.parse("+447700900124"), source="one-place"
            )
        assert len(provider.issued) == 2

    async def test_a_send_that_may_have_gone_out_counts_against_the_number(self) -> None:
        harness, provider = holding(resend_cooldowns=(timedelta(seconds=30),))
        provider.failure = DeliveryUncertainError(provider.name, "no answer")
        with pytest.raises(CodeMayHaveBeenSentError) as uncertain:
            await harness.service.request_challenge(NUMBER)
        assert uncertain.value.retry_after_seconds == 30

        with pytest.raises(RateLimitedError):
            await harness.service.request_challenge(NUMBER)

    async def test_a_number_the_provider_will_not_reach_is_its_refusal(self) -> None:
        harness, provider = holding()
        provider.failure = UnreachableNumberError(provider.name, "not a number")
        with pytest.raises(UnreachableNumberError):
            await harness.service.request_challenge(NUMBER)


class TestChecking:
    async def test_the_code_the_provider_sent_signs_in(self) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)

        pair = await harness.service.verify(issued.challenge_id, provider.code)

        assert pair.access_token
        assert provider.checked == [(NUMBER, provider.code)]
        assert await harness.users.find_by_number(NUMBER) is not None

    async def test_a_wrong_code_is_refused_and_consumes_an_attempt(self) -> None:
        harness, _ = holding()
        issued = await harness.service.request_challenge(NUMBER)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, WRONG)
        assert await stored_attempts(harness, issued.challenge_id) == 1

    async def test_the_attempts_run_out_before_the_provider_is_asked_again(self) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(AuthenticationError):
                await harness.service.verify(issued.challenge_id, WRONG)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, provider.code)
        assert len(provider.checked) == MAX_ATTEMPTS

    async def test_an_expired_code_is_refused_without_asking_the_provider(self) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)
        harness.clock.advance(CHALLENGE_LIFETIME.total_seconds() + 1)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, provider.code)
        assert provider.checked == []

    async def test_a_code_signs_in_once(self) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)
        await harness.service.verify(issued.challenge_id, provider.code)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, provider.code)
        assert len(provider.checked) == 1

    async def test_only_the_latest_challenge_is_checked(self) -> None:
        harness, provider = holding()
        old = await harness.service.request_challenge(NUMBER)
        new = await harness.service.request_challenge(NUMBER)

        with pytest.raises(AuthenticationError):
            await harness.service.verify(old.challenge_id, provider.code)
        assert await harness.service.verify(new.challenge_id, provider.code)

    async def test_wrong_codes_lock_the_number_and_then_even_the_right_one_is_refused(self) -> None:
        harness, provider = holding(failed_codes_per_number=2)
        issued = await harness.service.request_challenge(NUMBER)
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                await harness.service.verify(issued.challenge_id, WRONG)

        with pytest.raises(RateLimitedError):
            await harness.service.verify(issued.challenge_id, provider.code)
        assert len(provider.checked) == 2

    async def test_one_place_guessing_is_limited_before_the_provider_is_asked(self) -> None:
        limiter = CountingRateLimiter(NOW)
        provider = CheckingOTPProvider()
        harness = Harness(
            limiter=limiter,
            otp=provider,
            policy=AuthenticationPolicy(
                verifications_per_source=1, resend_cooldowns=(timedelta(0),)
            ),
        )
        issued = await harness.service.request_challenge(NUMBER)
        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, WRONG, source="one-place")
        with pytest.raises(RateLimitedError):
            await harness.service.verify(issued.challenge_id, provider.code, source="one-place")
        assert len(provider.checked) == 1

    @pytest.mark.parametrize(
        "failure",
        [
            ProviderError("checking", "HTTP 503", retryable=True),
            ProviderError("checking", "HTTP 401", retryable=False),
            DeliveryUncertainError("checking", "no answer"),
        ],
    )
    async def test_a_provider_that_cannot_answer_signs_nobody_in_and_counts_nothing(
        self, failure: ProviderError
    ) -> None:
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)
        provider.failure = failure

        with pytest.raises(CodeNotCheckedError) as unchecked:
            await harness.service.verify(issued.challenge_id, provider.code)
        assert unchecked.value.retry_after_seconds == CHECK_RETRY_AFTER_SECONDS
        assert unchecked.value.provider == "checking"
        assert await stored_attempts(harness, issued.challenge_id) == 0
        assert await harness.users.find_by_number(NUMBER) is None

        # The challenge is still open, and the same code works once the provider answers.
        assert await harness.service.verify(issued.challenge_id, provider.code)

    async def test_a_code_no_configured_provider_can_check_never_signs_in(self) -> None:
        # The provider serving the number changed between sending and checking.
        harness, provider = holding()
        issued = await harness.service.request_challenge(NUMBER)
        harness.service._otp = RecordingOTPProvider()

        with pytest.raises(AuthenticationError):
            await harness.service.verify(issued.challenge_id, provider.code)
        assert provider.checked == []


async def test_an_application_code_is_still_compared_here_when_the_provider_changes() -> None:
    # The other way round: a hashed code keeps working whoever serves the number now.
    harness = Harness()
    issued = await harness.service.request_challenge(NUMBER)
    harness.service._otp = CheckingOTPProvider()
    assert await harness.service.verify(issued.challenge_id, THE_CODE)
