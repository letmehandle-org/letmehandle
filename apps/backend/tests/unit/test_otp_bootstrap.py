"""Choosing the sign-in code provider: a real one when configured, complete, and never echoed."""

from __future__ import annotations

from typing import Any

import pytest

from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
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
)
from letmehandle.domain.errors import InvariantError
from letmehandle.main import create_app
from tests.support.config import REQUIRED_ENVIRONMENT, TEST_SIGNING_KEY, make_settings
from tests.support.simulated_sms import SMS_ACCOUNT, SMS_SENDER, SMS_TOKEN

SMS: dict[str, Any] = {
    "otp_provider": OTPProviderName.TWILIO_SMS,
    "sms_account_id": SMS_ACCOUNT,
    "sms_auth_token": SMS_TOKEN,
    "sms_from_number": SMS_SENDER,
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
    for name in ("SMS_ACCOUNT_ID", "SMS_AUTH_TOKEN", "SMS_FROM_NUMBER"):
        monkeypatch.setenv(name, "")
    settings = get_settings()
    assert settings.sms_from_number is None
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
