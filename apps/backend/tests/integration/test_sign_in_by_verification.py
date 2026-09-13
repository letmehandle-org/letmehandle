"""Signing in with a code the verification service made, over HTTP against a real database.

One deployment, two countries: numbers with calling code 91 are sent and checked their code by the
verification service, and every other number is texted a code the application made (D-041, D-042).
Both providers talk to simulated APIs, and each code is read off the message the way a person reads
it. What the service's failures become for somebody signing in is asserted here too.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from letmehandle.adapters.database.session import unit_of_work
from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from letmehandle.adapters.otp.twilio_verify import VerifyOTPProvider
from tests.integration.conftest import NUMBER, bearer, running
from tests.support.simulated_sms import SMS_ACCOUNT, SMS_SENDER, SMS_TOKEN, SimulatedSms
from tests.support.simulated_verify import (
    NOT_A_MOBILE,
    VERIFY_SERVICE,
    Behaviour,
    SimulatedVerify,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

# Shorter than any number in India's plan, so it can reach nobody.
INDIAN_NUMBER = "+91555001"


@pytest.fixture
def texts() -> SimulatedSms:
    return SimulatedSms()


@pytest.fixture
def verification() -> SimulatedVerify:
    return SimulatedVerify()


def two_countries(texts: SimulatedSms, verification: SimulatedVerify) -> OTPProviderByCallingCode:
    """Texts for everybody, and the verification service for India, as bootstrap builds them."""
    return OTPProviderByCallingCode(
        default=SmsOTPProvider(
            account_id=SMS_ACCOUNT,
            auth_token=SMS_TOKEN,
            sender=SMS_SENDER,
            transport=texts.transport,
        ),
        by_calling_code={
            "91": VerifyOTPProvider(
                account_id=SMS_ACCOUNT,
                auth_token=SMS_TOKEN,
                service_id=VERIFY_SERVICE,
                transport=verification.transport,
            )
        },
    )


@pytest.fixture
async def api(
    session: object,
    database_url: str,
    schema: str,
    texts: SimulatedSms,
    verification: SimulatedVerify,
) -> AsyncIterator[Api]:
    provider = two_countries(texts, verification)
    try:
        async with running(database_url, schema, otp=provider) as ready:
            yield ready
    finally:
        await provider.aclose()


async def challenge(api: Api, number: str) -> str:
    response = await api.client.post("/v1/auth/challenge", json={"phone_number": number})
    assert response.status_code == 202, response.text
    challenge_id: str = response.json()["challenge_id"]
    return challenge_id


async def verify(api: Api, challenge_id: str, code: str) -> tuple[int, dict[str, object]]:
    response = await api.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
    )
    return response.status_code, response.json()


async def stored(api: Api, challenge_id: str) -> tuple[int, bool]:
    """The attempts counted against a challenge, and whether it holds a hash."""
    async with unit_of_work(api.app.state.session_factory) as session:
        row = (
            await session.execute(
                text("SELECT attempts, code_hash FROM otp_challenges WHERE id = :id"),
                {"id": challenge_id},
            )
        ).one()
    return row.attempts, row.code_hash is not None


async def test_an_indian_number_signs_in_through_verification_and_a_us_one_through_texts(
    api: Api, texts: SimulatedSms, verification: SimulatedVerify
) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    american = await challenge(api, NUMBER)

    indian_status, indian_tokens = await verify(api, indian, verification.last_code())
    american_status, american_tokens = await verify(api, american, texts.last_code())

    assert (indian_status, american_status) == (200, 200)
    assert [code.to for code in verification.texted] == [INDIAN_NUMBER]
    assert [message.to for message in texts.sent] == [NUMBER]
    # India's code is the service's, so nothing about it is stored; the US code is kept as a hash.
    assert await stored(api, indian) == (1, False)
    assert await stored(api, american) == (1, True)

    for number, tokens in ((INDIAN_NUMBER, indian_tokens), (NUMBER, american_tokens)):
        me = await api.client.get("/v1/me", headers=bearer(tokens))
        assert me.json()["phone_number"] == number


async def test_a_code_from_one_country_s_provider_does_not_sign_in_the_other(
    api: Api, texts: SimulatedSms, verification: SimulatedVerify
) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    american = await challenge(api, NUMBER)

    assert (await verify(api, indian, texts.last_code()))[0] == 401
    assert (await verify(api, american, verification.last_code()))[0] == 401
    assert await stored(api, indian) == (1, False)
    assert await stored(api, american) == (1, True)


async def test_a_wrong_code_counts_and_the_right_one_still_signs_in(
    api: Api, verification: SimulatedVerify
) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    status, body = await verify(api, indian, "000000")
    assert status == 401
    assert body["error"] == "invalid_credentials"
    assert await stored(api, indian) == (1, False)

    assert (await verify(api, indian, verification.last_code()))[0] == 200


async def test_a_code_signs_in_once(api: Api, verification: SimulatedVerify) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    code = verification.last_code()
    assert (await verify(api, indian, code))[0] == 200
    assert (await verify(api, indian, code))[0] == 401


async def test_a_code_the_service_let_expire_is_refused_like_any_wrong_code(
    api: Api, verification: SimulatedVerify
) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    code = verification.last_code()
    verification.expire(INDIAN_NUMBER)

    assert (await verify(api, indian, code))[0] == 401
    assert await stored(api, indian) == (1, False)


@pytest.mark.parametrize("behaviour", [Behaviour.DOWN, Behaviour.SILENT, Behaviour.UNREACHABLE])
async def test_a_service_that_cannot_check_is_temporary_and_counts_nothing(
    api: Api, verification: SimulatedVerify, behaviour: Behaviour
) -> None:
    indian = await challenge(api, INDIAN_NUMBER)
    verification.behaviour = behaviour
    unchecked = await api.client.post(
        "/v1/auth/verify", json={"challenge_id": indian, "code": verification.last_code()}
    )
    assert unchecked.status_code == 503
    assert unchecked.json()["error"] == "provider_unavailable"
    assert int(unchecked.headers["Retry-After"]) > 0
    assert INDIAN_NUMBER not in unchecked.text
    assert await stored(api, indian) == (0, False)

    verification.behaviour = Behaviour.ANSWERS
    assert (await verify(api, indian, verification.last_code()))[0] == 200


async def test_a_number_the_service_cannot_text_is_told_so(
    api: Api, verification: SimulatedVerify
) -> None:
    verification.refuse(INDIAN_NUMBER, NOT_A_MOBILE)
    response = await api.client.post("/v1/auth/challenge", json={"phone_number": INDIAN_NUMBER})
    assert response.status_code == 422
    assert response.json()["error"] == "number_unreachable"


async def test_a_send_the_service_never_answered_counts_against_the_number(
    session: object,
    database_url: str,
    schema: str,
    texts: SimulatedSms,
    verification: SimulatedVerify,
) -> None:
    provider = two_countries(texts, verification)
    try:
        async with running(
            database_url, schema, otp=provider, resend_cooldowns=(timedelta(seconds=30),)
        ) as limited:
            verification.behaviour = Behaviour.SILENT
            uncertain = await limited.client.post(
                "/v1/auth/challenge", json={"phone_number": INDIAN_NUMBER}
            )
            assert uncertain.status_code == 503
            assert int(uncertain.headers["Retry-After"]) > 0

            verification.behaviour = Behaviour.ANSWERS
            again = await limited.client.post(
                "/v1/auth/challenge", json={"phone_number": INDIAN_NUMBER}
            )
            assert again.status_code == 429
            # The US number has allowances of its own.
            assert (await challenge(limited, NUMBER)) is not None
    finally:
        await provider.aclose()
