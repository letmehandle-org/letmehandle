"""Signing in with a texted code over HTTP against a real database and a simulated message API."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import structlog

from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from tests.integration.conftest import NUMBER, bearer, running
from tests.support.simulated_sms import (
    NOT_A_MOBILE,
    SMS_ACCOUNT,
    SMS_SENDER,
    SMS_TOKEN,
    Behaviour,
    SimulatedSms,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

# The per-number allowance a person gets in an hour.
CODES_AN_HOUR = 5


@pytest.fixture
def service() -> SimulatedSms:
    return SimulatedSms()


@pytest.fixture
async def texting(
    session: object, database_url: str, schema: str, service: SimulatedSms
) -> AsyncIterator[Api]:
    provider = SmsOTPProvider(
        account_id=SMS_ACCOUNT,
        auth_token=SMS_TOKEN,
        sender=SMS_SENDER,
        transport=service.transport,
    )
    try:
        async with running(database_url, schema, otp=provider) as ready:
            yield ready
    finally:
        await provider.aclose()


async def test_a_code_read_off_the_text_message_signs_in(
    texting: Api, service: SimulatedSms
) -> None:
    challenge = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert challenge.status_code == 202

    [message] = service.sent
    assert message.to == NUMBER
    verified = await texting.client.post(
        "/v1/auth/verify",
        json={"challenge_id": challenge.json()["challenge_id"], "code": service.last_code()},
    )
    assert verified.status_code == 200

    me = await texting.client.get("/v1/me", headers=bearer(verified.json()))
    assert me.json()["phone_number"] == NUMBER


async def test_a_number_that_cannot_receive_a_text_is_told_so(
    texting: Api, service: SimulatedSms
) -> None:
    service.refuse(NUMBER, NOT_A_MOBILE)
    with structlog.testing.capture_logs() as logs:
        response = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

    assert response.status_code == 422
    assert response.json()["error"] == "number_unreachable"
    assert NUMBER not in response.text
    assert NUMBER not in repr(logs)


@pytest.mark.parametrize(
    "behaviour", [Behaviour.THROTTLES, Behaviour.DOWN, Behaviour.SILENT, Behaviour.UNREACHABLE]
)
async def test_a_provider_that_cannot_send_now_is_a_temporary_failure(
    texting: Api, service: SimulatedSms, behaviour: Behaviour
) -> None:
    service.behaviour = behaviour
    response = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

    assert response.status_code == 503
    assert response.json()["error"] == "provider_unavailable"
    assert "correlation_id" in response.json()


async def test_codes_that_were_never_sent_do_not_use_up_the_hourly_allowance(
    texting: Api, service: SimulatedSms
) -> None:
    # A provider outage does not count as a sign-in attempt.
    service.behaviour = Behaviour.DOWN
    for _ in range(CODES_AN_HOUR):
        failed = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert failed.status_code == 503

    service.behaviour = Behaviour.DELIVERS
    for _ in range(CODES_AN_HOUR):
        sent = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert sent.status_code == 202
    assert len(service.sent) == CODES_AN_HOUR


async def test_a_code_that_may_have_been_sent_counts_and_says_when_to_ask_again(
    session: object, database_url: str, schema: str, service: SimulatedSms
) -> None:
    # An unanswered request may have been delivered, so it counts against cooldown and budgets.
    provider = SmsOTPProvider(
        account_id=SMS_ACCOUNT, auth_token=SMS_TOKEN, sender=SMS_SENDER, transport=service.transport
    )
    try:
        async with running(
            database_url, schema, otp=provider, resend_cooldowns=(timedelta(seconds=30),)
        ) as texting:
            await _uncertain_then_refused(texting, service)
    finally:
        await provider.aclose()


async def _uncertain_then_refused(texting: Api, service: SimulatedSms) -> None:
    service.behaviour = Behaviour.SILENT
    uncertain = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert uncertain.status_code == 503
    assert uncertain.json()["error"] == "provider_unavailable"
    assert int(uncertain.headers["Retry-After"]) > 0

    service.behaviour = Behaviour.DELIVERS
    again = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert again.status_code == 429


async def test_a_service_that_could_not_be_reached_sent_nothing_and_counts_for_nothing(
    texting: Api, service: SimulatedSms
) -> None:
    service.behaviour = Behaviour.UNREACHABLE
    refused = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert refused.status_code == 503
    assert "Retry-After" not in refused.headers

    service.behaviour = Behaviour.DELIVERS
    sent = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert sent.status_code == 202


async def test_each_country_signs_in_with_a_code_from_its_own_provider(
    session: object, database_url: str, schema: str, service: SimulatedSms
) -> None:
    # Indian numbers go to their own provider; this one is too short to reach anybody.
    indian_number = "+91555001"
    default = MockOTPProvider(is_production=False)
    texting = SmsOTPProvider(
        account_id=SMS_ACCOUNT, auth_token=SMS_TOKEN, sender=SMS_SENDER, transport=service.transport
    )
    provider = OTPProviderByCallingCode(default=default, by_calling_code={"91": texting})
    try:
        async with running(database_url, schema, otp=provider) as api:
            indian = await api.client.post(
                "/v1/auth/challenge", json={"phone_number": indian_number}
            )
            american = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
            signed_in = [
                await api.client.post(
                    "/v1/auth/verify",
                    json={"challenge_id": challenge.json()["challenge_id"], "code": code},
                )
                for challenge, code in (
                    (indian, service.last_code()),
                    (american, default.sent[-1][1]),
                )
            ]
    finally:
        await provider.aclose()

    assert [message.to for message in service.sent] == [indian_number]
    assert [number.value for number, _ in default.sent] == [NUMBER]
    assert [response.status_code for response in signed_in] == [200, 200]
