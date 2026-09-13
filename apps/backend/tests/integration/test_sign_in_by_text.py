"""Signing in with a code sent as a text message, over HTTP against a real database.

The same stack as the sign-in suite with one difference: the code arrives through the provider a
production deployment runs with, talking to a simulated message API, and is read off the message
the way a person reads it. What the provider's failures become is asserted here too, because the
difference between them is what somebody signing in sees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import structlog

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


@pytest.mark.parametrize("behaviour", [Behaviour.THROTTLES, Behaviour.DOWN, Behaviour.SILENT])
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
    # An outage is nobody's attempt. Counting it would lock a person out for an hour for trying
    # to sign in while the provider was down.
    service.behaviour = Behaviour.DOWN
    for _ in range(CODES_AN_HOUR):
        failed = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert failed.status_code == 503

    service.behaviour = Behaviour.DELIVERS
    for _ in range(CODES_AN_HOUR):
        sent = await texting.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
        assert sent.status_code == 202
    assert len(service.sent) == CODES_AN_HOUR
