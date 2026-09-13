"""Lines by region: read from two variables, refused naming what is wrong and never a value."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from letmehandle.config.settings import ConfigurationError, Settings, TelephonyProviderName
from letmehandle.config.telephony_lines import (
    LineProviderName,
    parse_line_tokens,
    parse_telephony_lines,
)
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.region import IN, US
from tests.support.config import REQUIRED_ENVIRONMENT, TEST_TRANSCRIPT_KEYS, make_settings

# The Indian numbers are shorter than any in India's plan, so they reach nobody.
US_ENTRY = (
    "us:provider=twilio;regions=US;numbers=+12025550100|+12025550101;account=account-us;"
    "app=app-us;webhook=https://calls.example.com/"
)
IN_ENTRY = (
    "in:provider=twilio;regions=IN;numbers=+91555010;account=account-in;app=app-in;"
    "webhook=https://calls.example.com"
)
LINES = f"{US_ENTRY},{IN_ENTRY}"
TOKENS = "us:token-us,in:token-in"


def entry(**changes: str) -> str:
    """The India line with some of its keys given other values, or removed when given ``""``."""
    pairs = {
        "provider": "twilio",
        "regions": "IN",
        "numbers": "+91555010",
        "account": "account-in",
        "app": "app-in",
        "webhook": "https://calls.example.com",
    }
    pairs.update(changes)
    return "in:" + ";".join(f"{key}={value}" for key, value in pairs.items() if value)


class TestParsing:
    def test_every_line_is_read_in_order_with_its_regions_and_numbers(self) -> None:
        us, india = parse_telephony_lines(LINES)

        assert (us.name, us.provider) == ("us", LineProviderName.TWILIO)
        assert us.regions == frozenset({US})
        assert us.numbers == (
            PhoneNumber.parse("+12025550100"),
            PhoneNumber.parse("+12025550101"),
        )
        # Without the trailing slash, so appending a path cannot produce a URL nobody signed.
        assert us.webhook_base_url == "https://calls.example.com"
        assert (india.name, india.regions, india.account_id, india.app_id) == (
            "in",
            frozenset({IN}),
            "account-in",
            "app-in",
        )

    def test_a_line_may_serve_several_regions_or_every_region(self) -> None:
        (both,) = parse_telephony_lines(entry(regions="us|IN"))
        (anywhere,) = parse_telephony_lines(entry(regions="*"))

        assert both.regions == frozenset({US, IN})
        assert anywhere.regions is None

    @pytest.mark.parametrize(
        ("text", "problem"),
        [
            (" , ", "lists no lines"),
            ("provider=twilio", "does not start with a name"),
            (entry().replace("in:", "In:"), "does not start with a name"),
            (entry() + ";colour=blue", "is not one of"),
            (entry() + ";app", "is not one of"),
            (entry() + ";app=again", "gives app more than once"),
            (entry(account="", webhook=""), "does not give account, webhook"),
            (entry(provider="carrier-pigeon"), "names a provider that is not one of twilio"),
            (entry(regions="GB"), "no telephony region is called 'GB'"),
            (entry(numbers="+91555010|2025550143"), "number 2 is not an international number"),
            (entry(webhook="ftp://calls.example.com"), "webhook that is not an http URL"),
            (entry(webhook="https://calls.example.com/?a=b"), "query or a fragment"),
            (f"{IN_ENTRY},{IN_ENTRY}", "names the same line more than once"),
            (f"{IN_ENTRY},{entry(regions='IN').replace('in:', 'india:')}", "region IN"),
            (
                f"{entry(regions='*')},{entry(regions='*').replace('in:', 'rest:')}",
                "more than one line serving '*'",
            ),
        ],
    )
    def test_a_line_that_cannot_be_used_is_refused_naming_the_problem(
        self, text: str, problem: str
    ) -> None:
        with pytest.raises(ValueError, match="TELEPHONY_LINES") as failure:
            parse_telephony_lines(text)
        assert problem in str(failure.value)
        # An account id or a number pasted into an issue is still an account id or a number.
        assert "account-in" not in str(failure.value)
        assert "2025550143" not in str(failure.value)

    def test_tokens_are_read_by_line(self) -> None:
        assert parse_line_tokens(TOKENS) == {"us": "token-us", "in": "token-in"}

    @pytest.mark.parametrize(
        ("text", "problem"),
        [
            ("token-us", "entry 1 is not in the form"),
            ("us:", "entry 1 is not in the form"),
            ("us:token-us,us:token-again", "more than one token"),
            (" , ", "lists no tokens"),
        ],
    )
    def test_tokens_that_cannot_be_read_are_refused_without_repeating_one(
        self, text: str, problem: str
    ) -> None:
        with pytest.raises(ValueError, match="TELEPHONY_LINE_AUTH_TOKENS") as failure:
            parse_line_tokens(text)
        assert problem in str(failure.value)
        assert "token-" not in str(failure.value)


class TestSettings:
    @pytest.fixture(autouse=True)
    def _required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name, value in REQUIRED_ENVIRONMENT.items():
            monkeypatch.setenv(name, value)

    def test_lines_and_their_tokens_are_read_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEPHONY_LINES", LINES)
        monkeypatch.setenv("TELEPHONY_LINE_AUTH_TOKENS", TOKENS)
        settings = Settings()

        us, india = settings.require_telephony_lines()

        assert (us.name, us.auth_token, india.name, india.auth_token) == (
            "us",
            "token-us",
            "in",
            "token-in",
        )
        assert "token-us" not in repr(settings)
        assert "token-us" not in repr(us)

    def test_blank_line_variables_count_as_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEPHONY_LINES", "")
        monkeypatch.setenv("TELEPHONY_LINE_AUTH_TOKENS", "")
        settings = Settings()

        assert settings.telephony_lines is None
        assert settings.require_telephony_lines() == ()

    def test_malformed_lines_stop_the_process_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEPHONY_LINE_AUTH_TOKENS", "not-a-pair")
        with pytest.raises(ValidationError, match="TELEPHONY_LINE_AUTH_TOKENS") as failure:
            Settings()
        assert "not-a-pair" not in str(failure.value)

    def test_lines_without_tokens_are_refused(self) -> None:
        with pytest.raises(ConfigurationError, match="TELEPHONY_LINE_AUTH_TOKENS must be set"):
            make_settings(telephony_lines=LINES).require_telephony_lines()

    def test_every_line_needs_a_token_and_no_token_names_another(self) -> None:
        settings = make_settings(telephony_lines=LINES, telephony_line_auth_tokens="us:t,uk:t")
        with pytest.raises(ConfigurationError) as failure:
            settings.require_telephony_lines()
        assert "missing ['in'], unknown ['uk']" in str(failure.value)

    def test_the_single_line_variables_are_refused_beside_lines_by_region(self) -> None:
        # Which of the two a deployment meant decides where every user forwards their calls.
        settings = make_settings(
            telephony_lines=LINES,
            telephony_line_auth_tokens=TOKENS,
            telephony_provider=TelephonyProviderName.TWILIO,
            telephony_numbers=(PhoneNumber.parse("+12025550100"),),
        )
        with pytest.raises(ConfigurationError) as failure:
            settings.require_telephony_lines()
        assert "TELEPHONY_PROVIDER, TELEPHONY_NUMBERS configure a single line" in str(failure.value)

    def test_lines_are_calls_and_need_somewhere_to_record_them(self) -> None:
        settings = make_settings(telephony_lines=LINES, telephony_line_auth_tokens=TOKENS)
        with pytest.raises(ConfigurationError, match="DATABASE_URL, TRANSCRIPT_ENCRYPTION_KEYS"):
            settings.require_telephony_configuration()

    def test_complete_lines_with_storage_are_accepted(self) -> None:
        make_settings(
            telephony_lines=LINES,
            telephony_line_auth_tokens=TOKENS,
            database_url="postgresql+asyncpg://nobody:nothing@127.0.0.1:1/absent",
            transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
        ).require_telephony_configuration()
