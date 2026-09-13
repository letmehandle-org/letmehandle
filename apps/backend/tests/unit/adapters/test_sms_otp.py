"""The provider that sends a sign-in code as a text message, against a simulated message API."""

from __future__ import annotations

from urllib.parse import parse_qsl

import pytest
import structlog

from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from letmehandle.domain.errors import ProviderError, UnreachableNumberError
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.support.simulated_sms import (
    NOT_A_MOBILE,
    NOT_A_NUMBER,
    REGION_NOT_ENABLED,
    SMS_ACCOUNT,
    SMS_SENDER,
    SMS_TOKEN,
    UNSUBSCRIBED,
    Behaviour,
    SimulatedSms,
)

NUMBER = PhoneNumber.parse("+12025550143")


def provider(service: SimulatedSms, *, token: str = SMS_TOKEN) -> SmsOTPProvider:
    return SmsOTPProvider(
        account_id=SMS_ACCOUNT, auth_token=token, sender=SMS_SENDER, transport=service.transport
    )


async def test_the_code_is_sent_to_the_number_from_the_configured_sender() -> None:
    service = SimulatedSms()
    sms = provider(service)
    await sms.send(NUMBER, "424242")
    await sms.aclose()

    [message] = service.sent
    assert message.to == NUMBER.value
    assert message.sender == SMS_SENDER.value
    assert "424242" in message.body
    assert service.last_code() == "424242"


async def test_nothing_but_the_number_the_sender_and_the_message_is_sent() -> None:
    service = SimulatedSms()
    sms = provider(service)
    await sms.send(NUMBER, "424242")
    await sms.aclose()

    fields = [name for name, _ in parse_qsl(service.requests[-1].content.decode())]
    assert sorted(fields) == ["Body", "From", "To"]


def test_it_delivers_real_codes_so_it_is_safe_for_production_and_fixes_none() -> None:
    sms = provider(SimulatedSms())
    assert sms.is_safe_for_production
    assert sms.fixed_code is None
    assert sms.name == "twilio-sms"


@pytest.mark.parametrize("code", [NOT_A_NUMBER, NOT_A_MOBILE, UNSUBSCRIBED])
async def test_a_number_the_provider_will_not_deliver_to_is_unreachable(code: int) -> None:
    service = SimulatedSms()
    service.refuse(NUMBER.value, code)
    sms = provider(service)
    with pytest.raises(UnreachableNumberError) as raised:
        await sms.send(NUMBER, "424242")
    await sms.aclose()
    assert not raised.value.retryable
    # The reason names the refusal, and never the number or the code.
    assert str(code) in raised.value.reason
    assert NUMBER.value not in str(raised.value)
    assert "424242" not in str(raised.value)


async def test_a_refusal_about_the_account_is_not_blamed_on_the_number() -> None:
    service = SimulatedSms()
    service.refuse(NUMBER.value, REGION_NOT_ENABLED)
    sms = provider(service)
    with pytest.raises(ProviderError) as raised:
        await sms.send(NUMBER, "424242")
    await sms.aclose()
    assert not isinstance(raised.value, UnreachableNumberError)
    assert not raised.value.retryable


@pytest.mark.parametrize(
    "behaviour", [Behaviour.THROTTLES, Behaviour.DOWN, Behaviour.SILENT, Behaviour.UNREACHABLE]
)
async def test_throttling_and_an_outage_are_worth_trying_again(behaviour: Behaviour) -> None:
    service = SimulatedSms()
    service.behaviour = behaviour
    sms = provider(service)
    with pytest.raises(ProviderError) as raised:
        await sms.send(NUMBER, "424242")
    await sms.aclose()
    assert not isinstance(raised.value, UnreachableNumberError)
    assert raised.value.retryable
    assert service.sent == []


async def test_wrong_credentials_are_a_failure_nobody_retries_away() -> None:
    service = SimulatedSms()
    sms = provider(service, token="not-the-token")
    with pytest.raises(ProviderError) as raised:
        await sms.send(NUMBER, "424242")
    await sms.aclose()
    assert not raised.value.retryable
    assert "401" in raised.value.reason


async def test_nothing_personal_is_logged_whether_it_is_sent_or_refused() -> None:
    service = SimulatedSms()
    sms = provider(service)
    with structlog.testing.capture_logs() as logs:
        await sms.send(NUMBER, "424242")
        service.refuse(NUMBER.value, NOT_A_NUMBER)
        with pytest.raises(UnreachableNumberError):
            await sms.send(NUMBER, "515151")
    await sms.aclose()

    assert logs
    written = repr(logs)
    for secret in (NUMBER.value, NUMBER.masked, "424242", "515151", SMS_TOKEN):
        assert secret not in written
