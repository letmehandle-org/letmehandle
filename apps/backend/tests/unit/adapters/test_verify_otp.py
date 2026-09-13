"""The provider whose verification service makes, texts and checks the code, simulated."""

from __future__ import annotations

from urllib.parse import parse_qsl

import httpx
import pytest
import structlog

from letmehandle.adapters.otp import twilio_verify as verify_module
from letmehandle.adapters.otp.twilio_verify import VerifyOTPProvider
from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    DeliveryUncertainError,
    ProviderError,
    UnreachableNumberError,
)
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.observability.logging import configure_logging
from tests.support.config import make_settings
from tests.support.simulated_sms import SMS_ACCOUNT, SMS_TOKEN
from tests.support.simulated_verify import (
    CHECKS_PER_VERIFICATION,
    DELIVERY_BLOCKED,
    INVALID_PARAMETER,
    NOT_A_MOBILE,
    NOT_A_NUMBER,
    VERIFY_SERVICE,
    Behaviour,
    SimulatedVerify,
)

# Shorter than any number in India's plan, so it can reach nobody.
NUMBER = PhoneNumber.parse("+91555001")


def provider(
    service: SimulatedVerify, *, token: str = SMS_TOKEN, service_id: str = VERIFY_SERVICE
) -> VerifyOTPProvider:
    return VerifyOTPProvider(
        account_id=SMS_ACCOUNT,
        auth_token=token,
        service_id=service_id,
        transport=service.transport,
    )


def fields_of(request: httpx.Request) -> list[str]:
    return sorted(name for name, _ in parse_qsl(request.content.decode()))


async def test_the_service_texts_its_own_code_and_approves_it_once() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    await verify.send_own_code(NUMBER)
    [texted] = service.texted

    assert texted.to == NUMBER.value
    assert await verify.check(NUMBER, service.last_code()) is True
    assert await verify.check(NUMBER, service.last_code()) is False
    await verify.aclose()


async def test_only_the_number_and_the_channel_or_the_code_are_sent() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    await verify.send_own_code(NUMBER)
    await verify.check(NUMBER, "000000")
    await verify.aclose()

    started, checked = service.requests
    assert fields_of(started) == ["Channel", "To"]
    assert dict(parse_qsl(started.content.decode()))["Channel"] == "sms"
    assert fields_of(checked) == ["Code", "To"]
    assert started.url.path.endswith(f"/Services/{VERIFY_SERVICE}/Verifications")
    assert checked.url.path.endswith(f"/Services/{VERIFY_SERVICE}/VerificationCheck")


async def test_a_wrong_code_is_not_approved_and_the_right_one_still_is() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    await verify.send_own_code(NUMBER)

    assert await verify.check(NUMBER, "000000") is False
    assert await verify.check(NUMBER, service.last_code()) is True
    await verify.aclose()


async def test_a_code_expired_or_never_sent_is_not_approved() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    assert await verify.check(NUMBER, "000000") is False

    await verify.send_own_code(NUMBER)
    code = service.last_code()
    service.expire(NUMBER.value)
    assert await verify.check(NUMBER, code) is False
    await verify.aclose()


async def test_a_verification_out_of_guesses_is_not_approved_even_with_the_right_code() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    await verify.send_own_code(NUMBER)
    for _ in range(CHECKS_PER_VERIFICATION):
        assert await verify.check(NUMBER, "000000") is False

    assert await verify.check(NUMBER, service.last_code()) is False
    await verify.aclose()


def test_it_delivers_real_codes_so_it_is_safe_for_production_and_fixes_none() -> None:
    verify = provider(SimulatedVerify())
    assert verify.is_safe_for_production
    assert verify.fixed_code is None
    assert verify.issues_its_own_codes(NUMBER)
    assert verify.name == "twilio-verify"


async def test_it_cannot_send_a_code_it_did_not_make() -> None:
    service = SimulatedVerify()
    verify = provider(service)
    with pytest.raises(CapabilityNotSupportedError):
        await verify.send(NUMBER, "424242")
    await verify.aclose()
    assert service.requests == []


@pytest.mark.parametrize("code", [INVALID_PARAMETER, NOT_A_NUMBER, NOT_A_MOBILE])
async def test_a_number_the_service_will_not_deliver_to_is_unreachable(code: int) -> None:
    service = SimulatedVerify()
    service.refuse(NUMBER.value, code)
    verify = provider(service)
    with pytest.raises(UnreachableNumberError) as raised:
        await verify.send_own_code(NUMBER)
    await verify.aclose()
    assert not raised.value.retryable
    assert str(code) in raised.value.reason
    assert NUMBER.value not in str(raised.value)


async def test_a_refusal_about_the_account_is_not_blamed_on_the_number() -> None:
    service = SimulatedVerify()
    service.refuse(NUMBER.value, DELIVERY_BLOCKED)
    verify = provider(service)
    with pytest.raises(ProviderError) as raised:
        await verify.send_own_code(NUMBER)
    await verify.aclose()
    assert not isinstance(raised.value, UnreachableNumberError)


@pytest.mark.parametrize("behaviour", [Behaviour.THROTTLES, Behaviour.DOWN, Behaviour.UNREACHABLE])
async def test_throttling_and_an_outage_are_worth_trying_again_on_either_request(
    behaviour: Behaviour,
) -> None:
    service = SimulatedVerify()
    service.behaviour = behaviour
    verify = provider(service)
    for request in (verify.send_own_code(NUMBER), verify.check(NUMBER, "000000")):
        with pytest.raises(ProviderError) as raised:
            await request
        assert not isinstance(raised.value, (UnreachableNumberError, DeliveryUncertainError))
        assert raised.value.retryable
    await verify.aclose()
    assert service.texted == []


async def test_a_request_the_service_never_answered_may_have_been_acted_on() -> None:
    service = SimulatedVerify()
    service.behaviour = Behaviour.SILENT
    verify = provider(service)
    with pytest.raises(DeliveryUncertainError):
        await verify.send_own_code(NUMBER)
    with pytest.raises(DeliveryUncertainError):
        await verify.check(NUMBER, "000000")
    await verify.aclose()


async def test_wrong_credentials_are_a_failure_nobody_retries_away() -> None:
    service = SimulatedVerify()
    verify = provider(service, token="not-the-token")
    with pytest.raises(ProviderError) as raised:
        await verify.check(NUMBER, "000000")
    await verify.aclose()
    assert not raised.value.retryable
    assert "401" in raised.value.reason


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        (httpx.Response(200, text="not json"), "no JSON"),
        (httpx.Response(200, json=["approved"]), "no verification"),
        (httpx.Response(200, json={"valid": True}), "no verification"),
    ],
)
async def test_a_check_answered_without_a_status_is_no_answer(
    answer: httpx.Response, problem: str
) -> None:
    # Never read as approved: a service that cannot say signs nobody in.
    verify = VerifyOTPProvider(
        account_id=SMS_ACCOUNT,
        auth_token=SMS_TOKEN,
        service_id=VERIFY_SERVICE,
        transport=httpx.MockTransport(lambda _: answer),
    )
    with pytest.raises(ProviderError, match=problem) as raised:
        await verify.check(NUMBER, "000000")
    await verify.aclose()
    assert raised.value.retryable


async def test_nothing_personal_is_logged_whether_sent_refused_or_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = SimulatedVerify()
    verify = provider(service)
    configure_logging(make_settings(log_level="debug"))
    try:
        with structlog.testing.capture_logs() as logs:
            monkeypatch.setattr(
                verify_module, "logger", structlog.get_logger(verify_module.__name__)
            )
            await verify.send_own_code(NUMBER)
            code = service.last_code()
            await verify.check(NUMBER, "515151")
            await verify.check(NUMBER, code)
            await verify.check(NUMBER, code)
            service.refuse(NUMBER.value, NOT_A_NUMBER)
            with pytest.raises(UnreachableNumberError):
                await verify.send_own_code(NUMBER)
    finally:
        configure_logging(make_settings())
    await verify.aclose()

    assert [entry["event"] for entry in logs] == [
        "otp_code_sent",
        "otp_code_checked",
        "otp_code_checked",
        "otp_code_checked",
        "otp_code_undeliverable",
    ]
    assert [entry.get("approved") for entry in logs[1:4]] == [False, True, False]
    written = repr(logs)
    for secret in (NUMBER.value, NUMBER.masked, code, "515151", SMS_TOKEN, VERIFY_SERVICE):
        assert secret not in written
