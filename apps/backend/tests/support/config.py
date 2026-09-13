"""Building settings for a test, without reaching for the host's environment."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Final

from pydantic import AnyHttpUrl, AnyWebsocketUrl, PostgresDsn, SecretStr

from letmehandle.config.settings import (
    APNsEnvironmentName,
    Environment,
    LogFormat,
    OTPProviderName,
    Settings,
    SpeechProviderName,
    TelephonyProviderName,
    parse_calling_codes,
    parse_llm_headers,
    parse_otp_providers,
    parse_voice_catalogue,
)
from letmehandle.config.telephony_lines import parse_telephony_lines

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.voice import Voice

# A database that is syntactically valid and certainly not listening. Port 1 is reserved and
# nothing in a test environment binds it, so "unreachable" is a property of the address rather
# than of whatever happens to be running on the machine.
UNREACHABLE_DATABASE = "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/absent"


# Long enough to satisfy the signer, and obviously not a real key. Tests that care about the
# key's own rules supply their own.
TEST_SIGNING_KEY = "test-signing-key-that-is-long-enough-to-be-accepted"


# Thirty-two bytes counting up from zero: a key nobody would choose, under an id that says so.
TEST_TRANSCRIPT_KEYS: Final = f"test-key:{base64.b64encode(bytes(range(32))).decode()}"


# A voice catalogue that says what it is. No speech service speaks these: they exist so that the
# application can start in a test, and names that sound like real voices would be mistaken for
# ones somebody could hear. `.env.example` and the development compose file carry the same text.
EXAMPLE_VOICES_TEXT: Final = (
    "example-voice-a:Example voice A (not a real voice):en,"
    "example-voice-b:Example voice B (not a real voice):en"
)
EXAMPLE_VOICES: Final[tuple[Voice, ...]] = parse_voice_catalogue(EXAMPLE_VOICES_TEXT)
EXAMPLE_DEFAULT_VOICE: Final = "example-voice-a"

# The variables a process needs before it will start at all, for tests that read the environment.
REQUIRED_ENVIRONMENT: Final = {
    "SPEECH_VOICES": EXAMPLE_VOICES_TEXT,
    "SPEECH_DEFAULT_VOICE": EXAMPLE_DEFAULT_VOICE,
}


def make_settings(
    *,
    app_env: Environment = Environment.TEST,
    log_level: str = "critical",
    log_format: LogFormat = LogFormat.CONSOLE,
    database_url: str | None = None,
    otp_provider: OTPProviderName = OTPProviderName.MOCK,
    otp_provider_by_calling_code: str = "",
    otp_allowed_calling_codes: str = "",
    sms_account_id: str | None = None,
    sms_auth_token: str | None = None,
    sms_from_number: PhoneNumber | None = None,
    sms_verify_service_id: str | None = None,
    auth_signing_key: str | None = TEST_SIGNING_KEY,
    speech_voices: tuple[Voice, ...] = EXAMPLE_VOICES,
    speech_default_voice: str = EXAMPLE_DEFAULT_VOICE,
    speech_provider: SpeechProviderName = SpeechProviderName.REALTIME,
    speech_languages: tuple[str, ...] = ("en",),
    speech_endpoint_url: str | None = None,
    speech_model: str | None = None,
    speech_agent_id: str | None = None,
    speech_transcription_model: str | None = None,
    speech_api_key: str | None = None,
    transcript_encryption_keys: str | None = None,
    telephony_provider: TelephonyProviderName | None = None,
    telephony_account_id: str | None = None,
    telephony_auth_token: str | None = None,
    telephony_numbers: tuple[PhoneNumber, ...] | None = None,
    telephony_app_id: str | None = None,
    telephony_webhook_base_url: str | None = None,
    telephony_lines: str | None = None,
    telephony_line_auth_tokens: str | None = None,
    call_max_duration_seconds: int = 14_400,
    llm_base_url: str | None = None,
    llm_api_key: str | None = None,
    llm_model: str | None = None,
    llm_headers: str = "",
    llm_timeout_seconds: float = 20,
    apns_key_id: str | None = None,
    apns_team_id: str | None = None,
    apns_private_key: str | None = None,
    apns_topic: str | None = None,
    apns_environment: APNsEnvironmentName | None = None,
    fcm_project_id: str | None = None,
    fcm_service_account_json: str | None = None,
    tracing_otlp_endpoint: str | None = None,
    diagnostics_token: str | None = None,
) -> Settings:
    """Settings with every field stated explicitly.

    Every value is passed, so a test never inherits a default that later changes underneath it,
    and never picks up a variable that happens to be set on the machine running it.
    """
    return Settings(
        app_env=app_env,
        log_level=log_level,
        log_format=log_format,
        database_url=PostgresDsn(database_url) if database_url is not None else None,
        otp_provider=otp_provider,
        otp_provider_by_calling_code=parse_otp_providers(otp_provider_by_calling_code),
        otp_allowed_calling_codes=parse_calling_codes(otp_allowed_calling_codes),
        sms_account_id=sms_account_id,
        sms_auth_token=SecretStr(sms_auth_token) if sms_auth_token is not None else None,
        sms_from_number=sms_from_number,
        sms_verify_service_id=sms_verify_service_id,
        auth_signing_key=SecretStr(auth_signing_key) if auth_signing_key is not None else None,
        speech_provider=speech_provider,
        speech_languages=speech_languages,
        speech_endpoint_url=(
            AnyWebsocketUrl(speech_endpoint_url) if speech_endpoint_url is not None else None
        ),
        speech_model=speech_model,
        speech_agent_id=speech_agent_id,
        speech_transcription_model=speech_transcription_model,
        speech_api_key=SecretStr(speech_api_key) if speech_api_key is not None else None,
        speech_voices=speech_voices,
        speech_default_voice=speech_default_voice,
        transcript_encryption_keys=(
            SecretStr(transcript_encryption_keys)
            if transcript_encryption_keys is not None
            else None
        ),
        telephony_provider=telephony_provider,
        telephony_account_id=telephony_account_id,
        telephony_auth_token=(
            SecretStr(telephony_auth_token) if telephony_auth_token is not None else None
        ),
        telephony_numbers=telephony_numbers,
        telephony_app_id=telephony_app_id,
        telephony_webhook_base_url=(
            AnyHttpUrl(telephony_webhook_base_url)
            if telephony_webhook_base_url is not None
            else None
        ),
        telephony_lines=None if telephony_lines is None else parse_telephony_lines(telephony_lines),
        telephony_line_auth_tokens=(
            SecretStr(telephony_line_auth_tokens)
            if telephony_line_auth_tokens is not None
            else None
        ),
        call_max_duration_seconds=call_max_duration_seconds,
        llm_base_url=AnyHttpUrl(llm_base_url) if llm_base_url is not None else None,
        llm_api_key=SecretStr(llm_api_key) if llm_api_key is not None else None,
        llm_model=llm_model,
        llm_headers=parse_llm_headers(llm_headers),
        llm_timeout_seconds=llm_timeout_seconds,
        apns_key_id=apns_key_id,
        apns_team_id=apns_team_id,
        apns_private_key=SecretStr(apns_private_key) if apns_private_key is not None else None,
        apns_topic=apns_topic,
        apns_environment=apns_environment,
        fcm_project_id=fcm_project_id,
        fcm_service_account_json=(
            SecretStr(fcm_service_account_json) if fcm_service_account_json is not None else None
        ),
        tracing_otlp_endpoint=(
            AnyHttpUrl(tracing_otlp_endpoint) if tracing_otlp_endpoint is not None else None
        ),
        diagnostics_token=SecretStr(diagnostics_token) if diagnostics_token is not None else None,
    )
