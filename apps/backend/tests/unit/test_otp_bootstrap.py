"""Choosing the sign-in code provider: a real one when configured, complete, and never echoed."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from letmehandle.adapters.otp.twilio_verify import VerifyOTPProvider
from letmehandle.bootstrap import (
    build_container,
    build_reported_calls,
    build_voice_provider,
    close_providers,
)
from letmehandle.config.settings import (
    ConfigurationError,
    Environment,
    OTPProviderName,
    Settings,
    get_settings,
    parse_otp_providers,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.main import create_app
from tests.support.config import REQUIRED_ENVIRONMENT, TEST_SIGNING_KEY, make_settings
from tests.support.simulated_sms import SMS_ACCOUNT, SMS_SENDER, SMS_TOKEN
from tests.support.simulated_verify import VERIFY_SERVICE

SMS: dict[str, Any] = {
    "otp_provider": OTPProviderName.TWILIO_SMS,
    "sms_account_id": SMS_ACCOUNT,
    "sms_auth_token": SMS_TOKEN,
    "sms_from_number": SMS_SENDER,
}

VERIFY: dict[str, Any] = {
    "otp_provider": OTPProviderName.TWILIO_VERIFY,
    "sms_account_id": SMS_ACCOUNT,
    "sms_auth_token": SMS_TOKEN,
    "sms_verify_service_id": VERIFY_SERVICE,
}


def container_for(settings: Settings) -> Any:
    return build_container(
        settings, voices=build_voice_provider(settings), reported_calls=build_reported_calls()
    )


async def test_the_text_message_provider_is_chosen_when_configured() -> None:
    container = container_for(make_settings(**SMS))
    assert isinstance(container.otp, SmsOTPProvider)
    assert container.otp.is_safe_for_production
    await close_providers(container)


def test_the_mock_stays_the_default() -> None:
    assert isinstance(container_for(make_settings()).otp, MockOTPProvider)


async def test_a_production_configuration_with_a_real_provider_starts() -> None:
    # The whole lifespan, not only the container: this is the deployment that could not start
    # before a provider that delivers codes existed.
    app = create_app(make_settings(app_env=Environment.PRODUCTION, **SMS))
    async with app.router.lifespan_context(app):
        assert app.state.container.otp.is_safe_for_production


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        (("sms_account_id",), "SMS_ACCOUNT_ID"),
        (("sms_auth_token",), "SMS_AUTH_TOKEN"),
        (("sms_from_number",), "SMS_FROM_NUMBER"),
        (
            ("sms_account_id", "sms_auth_token", "sms_from_number"),
            "SMS_ACCOUNT_ID, SMS_AUTH_TOKEN, SMS_FROM_NUMBER",
        ),
    ],
)
def test_an_incomplete_account_stops_startup_naming_every_missing_variable(
    missing: tuple[str, ...], named: str
) -> None:
    settings = make_settings(**{**SMS, **dict.fromkeys(missing)})
    with pytest.raises(ConfigurationError, match=named) as caught:
        container_for(settings)
    assert SMS_TOKEN not in str(caught.value)


def test_production_with_the_mock_is_still_refused() -> None:
    with pytest.raises(InvariantError, match="cannot run in production"):
        container_for(make_settings(app_env=Environment.PRODUCTION))


def test_the_account_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AUTH_SIGNING_KEY", TEST_SIGNING_KEY)
    monkeypatch.setenv("OTP_PROVIDER", "twilio_sms")
    monkeypatch.setenv("SMS_ACCOUNT_ID", SMS_ACCOUNT)
    monkeypatch.setenv("SMS_AUTH_TOKEN", SMS_TOKEN)
    monkeypatch.setenv("SMS_FROM_NUMBER", "+1 (202) 555-0101")

    settings = get_settings()
    account = settings.require_sms_account()

    assert account.sender == SMS_SENDER
    assert account.auth_token == SMS_TOKEN
    assert SMS_TOKEN not in repr(settings)
    assert SMS_TOKEN not in repr(account)


def test_blank_account_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    for name in ("SMS_ACCOUNT_ID", "SMS_AUTH_TOKEN", "SMS_FROM_NUMBER", "SMS_VERIFY_SERVICE_ID"):
        monkeypatch.setenv(name, "")
    settings = get_settings()
    assert settings.sms_from_number is None
    assert settings.sms_verify_service_id is None
    assert settings.sms_auth_token is None


def test_a_sender_that_is_not_a_number_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SMS_FROM_NUMBER", "call-me-maybe")
    with pytest.raises(ConfigurationError, match="SMS_FROM_NUMBER") as caught:
        get_settings()
    assert "call-me-maybe" not in str(caught.value)


# ------------------------------------------------------------------ a provider per country


async def test_a_country_with_a_provider_of_its_own_is_sent_codes_by_it() -> None:
    container = container_for(
        make_settings(**SMS, otp_provider_by_calling_code="91:twilio_sms,44:mock")
    )
    assert isinstance(container.otp, OTPProviderByCallingCode)
    assert container.otp.name == "twilio-sms+44:mock,91:twilio-sms"
    # A country left on the mock makes the whole deployment unsafe to run in production.
    assert not container.otp.is_safe_for_production
    await close_providers(container)


def test_without_a_provider_per_country_the_default_is_the_provider() -> None:
    assert isinstance(container_for(make_settings(**SMS)).otp, SmsOTPProvider)


def test_a_country_on_the_text_message_provider_needs_its_account_whatever_the_default() -> None:
    with pytest.raises(ConfigurationError, match="SMS_ACCOUNT_ID"):
        container_for(make_settings(otp_provider_by_calling_code="91:twilio_sms"))


def test_production_refuses_a_country_left_on_the_mock() -> None:
    settings = make_settings(
        app_env=Environment.PRODUCTION, **SMS, otp_provider_by_calling_code="91:mock"
    )
    with pytest.raises(InvariantError, match="cannot run in production"):
        container_for(settings)


def test_providers_by_country_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTP_PROVIDER_BY_CALLING_CODE", "+91:twilio_sms, 1:mock")
    assert get_settings().otp_provider_by_calling_code == (
        ("91", OTPProviderName.TWILIO_SMS),
        ("1", OTPProviderName.MOCK),
    )


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("91", "entry 1 is not a calling code and one of mock, twilio_sms, twilio_verify"),
        ("1:mock,091:mock", "entry 2 is not a calling code"),
        ("91:carrier-pigeon", "entry 1 is not a calling code"),
        ("91:mock,91:twilio_sms", "names calling code 91 twice"),
    ],
)
def test_providers_by_country_that_cannot_be_used_are_refused(text: str, problem: str) -> None:
    with pytest.raises(ValueError, match="OTP_PROVIDER_BY_CALLING_CODE") as caught:
        parse_otp_providers(text)
    assert problem in str(caught.value)


def test_a_provider_for_a_country_codes_are_never_sent_to_is_refused() -> None:
    with pytest.raises(ValidationError, match="names 44, which OTP_ALLOWED_CALLING_CODES"):
        make_settings(
            otp_provider_by_calling_code="91:mock,44:mock", otp_allowed_calling_codes="1,91"
        )


# ------------------------------------------------------------------ the verification service


async def test_the_verification_service_provider_is_chosen_when_configured() -> None:
    container = container_for(make_settings(**VERIFY))
    assert isinstance(container.otp, VerifyOTPProvider)
    assert container.otp.is_safe_for_production
    await close_providers(container)


async def test_a_production_configuration_with_the_verification_service_starts() -> None:
    app = create_app(make_settings(app_env=Environment.PRODUCTION, **VERIFY))
    async with app.router.lifespan_context(app):
        assert app.state.container.otp.is_safe_for_production


@pytest.mark.parametrize(
    ("missing", "named"),
    [
        (("sms_verify_service_id",), "SMS_VERIFY_SERVICE_ID must be set"),
        (("sms_account_id",), "SMS_ACCOUNT_ID must be set"),
        (
            ("sms_account_id", "sms_auth_token", "sms_verify_service_id"),
            "SMS_ACCOUNT_ID, SMS_AUTH_TOKEN, SMS_VERIFY_SERVICE_ID must be set",
        ),
    ],
)
def test_an_incomplete_verification_service_stops_startup_naming_what_is_missing(
    missing: tuple[str, ...], named: str
) -> None:
    settings = make_settings(**{**VERIFY, **dict.fromkeys(missing)})
    with pytest.raises(ConfigurationError, match=named) as caught:
        container_for(settings)
    assert SMS_TOKEN not in str(caught.value)
    assert VERIFY_SERVICE not in str(caught.value)


async def test_india_on_the_verification_service_and_everybody_else_on_texts() -> None:
    container = container_for(
        make_settings(
            **SMS,
            sms_verify_service_id=VERIFY_SERVICE,
            otp_provider_by_calling_code="91:twilio_verify",
            otp_allowed_calling_codes="1,91",
        )
    )
    assert isinstance(container.otp, OTPProviderByCallingCode)
    assert container.otp.name == "twilio-sms+91:twilio-verify"
    assert container.otp.is_safe_for_production
    assert container.otp.issues_its_own_codes(PhoneNumber.parse("+91555001"))
    assert not container.otp.issues_its_own_codes(PhoneNumber.parse("+12025550143"))
    await close_providers(container)


def test_a_country_on_the_verification_service_needs_its_service_whatever_the_default() -> None:
    with pytest.raises(ConfigurationError, match="SMS_VERIFY_SERVICE_ID") as caught:
        container_for(make_settings(**SMS, otp_provider_by_calling_code="91:twilio_verify"))
    assert "SMS_FROM_NUMBER" not in str(caught.value)


def test_the_verification_service_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OTP_PROVIDER", "mock")
    monkeypatch.setenv("OTP_PROVIDER_BY_CALLING_CODE", "91:twilio_verify")
    monkeypatch.setenv("SMS_ACCOUNT_ID", SMS_ACCOUNT)
    monkeypatch.setenv("SMS_AUTH_TOKEN", SMS_TOKEN)
    monkeypatch.setenv("SMS_VERIFY_SERVICE_ID", VERIFY_SERVICE)

    settings = get_settings()
    verify = settings.require_verify_account()

    assert settings.otp_provider_by_calling_code == (("91", OTPProviderName.TWILIO_VERIFY),)
    assert verify.service_id == VERIFY_SERVICE
    assert SMS_TOKEN not in repr(verify)
