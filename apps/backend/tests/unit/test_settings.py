"""Configuration must fail loudly, at startup, naming what is wrong."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from letmehandle.config.settings import (
    ConfigurationError,
    Environment,
    LogFormat,
    Settings,
    SpeechProviderName,
    TelephonyProviderName,
    get_settings,
    parse_voice_catalogue,
)
from letmehandle.domain.ports.voice import Voice
from tests.support.config import (
    EXAMPLE_DEFAULT_VOICE,
    EXAMPLE_VOICES,
    REQUIRED_ENVIRONMENT,
    UNREACHABLE_DATABASE,
    make_settings,
)


@pytest.fixture
def required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_defaults_are_development() -> None:
    settings = Settings(speech_voices=EXAMPLE_VOICES, speech_default_voice=EXAMPLE_DEFAULT_VOICE)
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.log_format is LogFormat.CONSOLE
    assert settings.is_production is False


def test_production_is_recognised() -> None:
    assert make_settings(app_env=Environment.PRODUCTION).is_production is True


def test_production_without_a_signing_key_is_refused() -> None:
    # Checked at startup rather than at first use: a service that starts and then cannot
    # authenticate anybody is worse than one that does not start.
    with pytest.raises(ValidationError, match="AUTH_SIGNING_KEY"):
        make_settings(app_env=Environment.PRODUCTION, auth_signing_key=None)


def test_a_missing_signing_key_is_named_when_it_is_asked_for() -> None:
    with pytest.raises(ConfigurationError, match="AUTH_SIGNING_KEY"):
        make_settings(auth_signing_key=None).require_signing_key()


def test_the_signing_key_is_not_rendered_by_accident() -> None:
    # pydantic's SecretStr, so that a settings object in a log line or a traceback does not
    # hand over the key every token is signed with.
    settings = make_settings()
    assert "test-signing-key" not in repr(settings)
    assert settings.require_signing_key().startswith("test-signing-key")


@pytest.mark.parametrize("level", ["debug", "INFO", "Warning", "error", "critical"])
def test_known_log_levels_are_accepted_and_normalised(level: str) -> None:
    assert make_settings(log_level=level).log_level == level.lower()


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        make_settings(log_level="chatty")


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(app_env="staging")  # type: ignore[arg-type]


def test_settings_are_frozen() -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.log_level = "debug"


def test_missing_database_url_names_the_variable() -> None:
    settings = make_settings(database_url=None)
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        settings.require_database_url()


def test_database_url_is_returned_when_present() -> None:
    settings = make_settings(database_url=UNREACHABLE_DATABASE)
    assert settings.require_database_url().startswith("postgresql+asyncpg://")


def test_malformed_database_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url="not-a-url")


@pytest.mark.usefixtures("required_environment")
def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_invalid_environment_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid environment must stop the process with a message naming the variable."""
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        get_settings()


# -- Realtime speech ---------------------------------------------------------------------------


def test_speech_service_settings_are_optional_at_startup() -> None:
    settings = make_settings()
    assert settings.speech_endpoint_url is None
    assert settings.speech_model is None
    assert settings.speech_api_key is None


@pytest.mark.parametrize("url", ["wss://speech.example.com/v1/realtime", "ws://127.0.0.1:9000/v1"])
def test_a_websocket_endpoint_is_accepted(url: str) -> None:
    settings = Settings(
        speech_endpoint_url=url,  # type: ignore[arg-type]
        speech_voices=EXAMPLE_VOICES,
        speech_default_voice=EXAMPLE_DEFAULT_VOICE,
    )
    assert settings.speech_endpoint_url is not None
    assert settings.speech_endpoint_url.scheme == url.split(":", 1)[0]


def test_an_endpoint_that_is_not_a_websocket_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError, match="speech_endpoint_url"):
        Settings(
            speech_endpoint_url="https://speech.example.com/v1/realtime",  # type: ignore[arg-type]
            speech_voices=EXAMPLE_VOICES,
            speech_default_voice=EXAMPLE_DEFAULT_VOICE,
        )


@pytest.mark.usefixtures("required_environment")
def test_blank_speech_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # What a copied `.env.example` supplies before anybody fills it in.
    for name in ("SPEECH_ENDPOINT_URL", "SPEECH_MODEL", "SPEECH_AGENT_ID", "SPEECH_API_KEY"):
        monkeypatch.setenv(name, "")
    settings = get_settings()
    assert settings.speech_endpoint_url is None
    assert settings.speech_model is None
    assert settings.speech_agent_id is None
    assert settings.speech_api_key is None


def test_the_speech_protocol_defaults_to_the_one_existing_deployments_speak() -> None:
    settings = Settings(speech_voices=EXAMPLE_VOICES, speech_default_voice=EXAMPLE_DEFAULT_VOICE)
    assert settings.speech_provider is SpeechProviderName.REALTIME
    assert settings.speech_agent_id is None


@pytest.mark.usefixtures("required_environment")
def test_elevenlabs_is_chosen_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECH_PROVIDER", "elevenlabs")
    monkeypatch.setenv("SPEECH_AGENT_ID", "agent-example")
    settings = get_settings()
    assert settings.speech_provider is SpeechProviderName.ELEVENLABS
    assert settings.speech_agent_id == "agent-example"


@pytest.mark.usefixtures("required_environment")
def test_an_unknown_speech_protocol_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECH_PROVIDER", "carrier-pigeon")
    with pytest.raises(ConfigurationError, match="SPEECH_PROVIDER"):
        get_settings()


@pytest.mark.parametrize(
    ("endpoint", "agent_id", "missing"),
    [
        (None, None, "SPEECH_ENDPOINT_URL and SPEECH_AGENT_ID"),
        ("wss://speech.example.com/v1/convai/conversation", None, "SPEECH_AGENT_ID must"),
        (None, "agent-example", "SPEECH_ENDPOINT_URL must"),
    ],
)
def test_talking_to_an_agent_names_whatever_is_missing(
    endpoint: str | None, agent_id: str | None, missing: str
) -> None:
    settings = make_settings(
        speech_provider=SpeechProviderName.ELEVENLABS,
        speech_endpoint_url=endpoint,
        speech_agent_id=agent_id,
    )
    with pytest.raises(ConfigurationError, match=missing):
        settings.require_speech_agent()


def test_an_agent_that_is_configured_is_returned_with_its_endpoint() -> None:
    settings = make_settings(
        speech_provider=SpeechProviderName.ELEVENLABS,
        speech_endpoint_url="wss://speech.example.com/v1/convai/conversation",
        speech_agent_id="agent-example",
    )
    assert settings.require_speech_agent() == (
        "wss://speech.example.com/v1/convai/conversation",
        "agent-example",
    )


@pytest.mark.usefixtures("required_environment")
def test_a_blank_database_url_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # `.env.example` ships `DATABASE_URL=`, and copying it must start the application rather
    # than fail validating an empty string as a connection address.
    monkeypatch.setenv("DATABASE_URL", "")
    assert get_settings().database_url is None


@pytest.mark.usefixtures("required_environment")
def test_the_speech_key_is_not_rendered_by_accident(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECH_API_KEY", "speech-key-that-must-stay-out-of-logs")
    settings = get_settings()
    assert "speech-key-that-must-stay-out-of-logs" not in repr(settings)
    assert settings.speech_api_key is not None
    assert settings.speech_api_key.get_secret_value() == "speech-key-that-must-stay-out-of-logs"


# -- The voice catalogue ------------------------------------------------------------------------


def test_the_catalogue_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECH_VOICES", " first:First example:en|en-GB , second:Second example:fr ")
    monkeypatch.setenv("SPEECH_DEFAULT_VOICE", "second")

    settings = get_settings()

    assert settings.speech_voices == (
        Voice(id="first", name="First example", locales=("en", "en-GB")),
        Voice(id="second", name="Second example", locales=("fr",)),
    )
    assert settings.speech_default_voice == "second"


def test_settings_without_a_catalogue_can_be_read_by_what_has_no_use_for_one() -> None:
    # A database migration reads the settings for its URL. Refusing it for want of voices is a
    # deployment that cannot migrate until it has configured something unrelated.
    assert get_settings().speech_voices is None


def test_asking_for_the_catalogue_without_one_names_what_to_set() -> None:
    with pytest.raises(ConfigurationError, match="SPEECH_VOICES and SPEECH_DEFAULT_VOICE"):
        get_settings().require_voice_catalogue()


def test_half_a_catalogue_is_refused_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPEECH_DEFAULT_VOICE", "first")
    with pytest.raises(ConfigurationError, match="set together or not at all"):
        get_settings()


@pytest.mark.parametrize(
    "text",
    [
        "no-name-or-locale",
        "id:name",
        "id:name:en:extra",
        ":name:en",
        "id::en",
        "id:name:",
        "id:name:en|",
    ],
)
def test_a_malformed_entry_is_named_with_the_format_it_should_have(text: str) -> None:
    with pytest.raises(ValueError, match=r"SPEECH_VOICES entry .* is not in the form 'id:Display"):
        parse_voice_catalogue(text)


@pytest.mark.parametrize("text", ["", " , ,"])
def test_a_catalogue_with_no_voices_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="SPEECH_VOICES lists no voices"):
        parse_voice_catalogue(text)


def test_a_repeated_voice_id_is_refused() -> None:
    with pytest.raises(ValueError, match=r"the same id more than once: \['one'\]"):
        parse_voice_catalogue("one:One:en,two:Two:en,one:Again:fr")


def test_a_default_voice_outside_the_catalogue_is_refused() -> None:
    with pytest.raises(ValidationError, match="SPEECH_DEFAULT_VOICE 'absent' is not one of"):
        make_settings(speech_default_voice="absent")


# --------------------------------------------------------------------------- streaming telephony

A_TELEPHONY_ENVIRONMENT = {
    "TELEPHONY_PROVIDER": "twilio",
    "TELEPHONY_ACCOUNT_ID": "account-for-tests",
    "TELEPHONY_AUTH_TOKEN": "token-for-tests",
    "TELEPHONY_NUMBERS": "+1 202 555 0143, +12025550144",
    "TELEPHONY_APP_ID": "app-for-tests",
    "TELEPHONY_WEBHOOK_BASE_URL": "https://calls.example.com/",
}


def test_streaming_telephony_is_optional_at_startup() -> None:
    settings = make_settings()
    assert settings.telephony_provider is None
    with pytest.raises(ConfigurationError) as failure:
        settings.require_streaming_telephony()
    for name in (
        "TELEPHONY_ACCOUNT_ID",
        "TELEPHONY_AUTH_TOKEN",
        "TELEPHONY_NUMBERS",
        "TELEPHONY_APP_ID",
        "TELEPHONY_WEBHOOK_BASE_URL",
    ):
        assert name in str(failure.value)


def test_streaming_telephony_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, required_environment: None
) -> None:
    for name, value in A_TELEPHONY_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.telephony_provider is TelephonyProviderName.TWILIO
    telephony = settings.require_streaming_telephony()
    assert [number.value for number in telephony.numbers] == ["+12025550143", "+12025550144"]
    # Without the trailing slash, so appending a path cannot produce a URL nobody signed.
    assert telephony.webhook_base_url == "https://calls.example.com"
    assert telephony.auth_token == "token-for-tests"


def test_only_what_is_missing_is_named(
    monkeypatch: pytest.MonkeyPatch, required_environment: None
) -> None:
    for name, value in A_TELEPHONY_ENVIRONMENT.items():
        if name != "TELEPHONY_APP_ID":
            monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as failure:
        Settings().require_streaming_telephony()
    assert "TELEPHONY_APP_ID" in str(failure.value)
    assert "TELEPHONY_ACCOUNT_ID" not in str(failure.value)


def test_blank_telephony_variables_count_as_unset(
    monkeypatch: pytest.MonkeyPatch, required_environment: None
) -> None:
    for name in A_TELEPHONY_ENVIRONMENT:
        monkeypatch.setenv(name, "")
    settings = Settings()
    assert settings.telephony_provider is None
    assert settings.telephony_numbers is None
    assert settings.telephony_webhook_base_url is None


def test_the_auth_token_is_not_rendered_by_accident(
    monkeypatch: pytest.MonkeyPatch, required_environment: None
) -> None:
    monkeypatch.setenv("TELEPHONY_AUTH_TOKEN", "token-that-must-not-appear")
    settings = Settings()
    assert "token-that-must-not-appear" not in repr(settings)
    assert "token-that-must-not-appear" not in str(settings.model_dump())


@pytest.mark.parametrize("text", ["2025550143", ", ,"])
def test_a_number_list_that_cannot_be_read_is_refused_without_repeating_it(
    monkeypatch: pytest.MonkeyPatch, required_environment: None, text: str
) -> None:
    monkeypatch.setenv("TELEPHONY_NUMBERS", text)
    with pytest.raises(ValidationError) as failure:
        Settings()
    assert "TELEPHONY_NUMBERS" in str(failure.value)


@pytest.mark.parametrize(
    "url", ["https://calls.example.com/?a=b", "https://calls.example.com/#part"]
)
def test_a_base_url_with_a_query_or_fragment_is_refused(
    monkeypatch: pytest.MonkeyPatch, required_environment: None, url: str
) -> None:
    monkeypatch.setenv("TELEPHONY_WEBHOOK_BASE_URL", url)
    with pytest.raises(ValidationError, match="query or a fragment"):
        Settings()
