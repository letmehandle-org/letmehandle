"""The only place this application reads its environment."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Final

from pydantic import (
    AnyHttpUrl,
    AnyWebsocketUrl,
    BeforeValidator,
    Field,
    PostgresDsn,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.voice import Voice

if TYPE_CHECKING:
    from collections.abc import Mapping


class Environment(StrEnum):
    """Where this process is running, which decides what it is allowed to do."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class OTPProviderName(StrEnum):
    """Which provider delivers sign-in codes."""

    MOCK = "mock"
    TWILIO_SMS = "twilio_sms"


class SpeechProviderName(StrEnum):
    """Which protocol the speech service speaks, and so which adapter talks to it."""

    REALTIME = "realtime"
    ELEVENLABS = "elevenlabs"


class APNsEnvironmentName(StrEnum):
    """Which of Apple's push servers a deployment talks to.

    No default. A device token belongs to one environment and is `BadDeviceToken` to the other,
    and that response removes the token — so a production deployment left pointing at the sandbox
    would quietly delete every user's device. It is safer to refuse to guess.
    """

    SANDBOX = "sandbox"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class APNsCredentials:
    """Everything direct delivery to iOS needs, once it is known to be complete."""

    key_id: str
    team_id: str
    private_key: str
    topic: str
    environment: APNsEnvironmentName


@dataclass(frozen=True, slots=True)
class FCMCredentials:
    """Everything direct delivery to Android needs, once it is known to be complete."""

    project_id: str
    service_account_json: str


class TelephonyProviderName(StrEnum):
    """Which call transport carries this deployment's calls, if any.

    Two kinds, not two suppliers of one kind: a programmable telephony account that streams a
    call's audio, and the user's own handset screening calls before they ring.
    """

    TWILIO = "twilio"
    ANDROID_NATIVE = "android_native"


@dataclass(frozen=True, slots=True)
class SmsAccount:
    """Everything the text-message code provider needs, present and checked."""

    account_id: str
    auth_token: str = field(repr=False)
    sender: PhoneNumber


@dataclass(frozen=True, slots=True)
class StreamingTelephony:
    """Everything a streaming call transport needs, present and checked.

    `webhook_base_url` has no trailing slash, so a path can be appended to it without producing
    a URL that differs by one character from the one the provider signed.
    """

    account_id: str
    auth_token: str
    numbers: tuple[PhoneNumber, ...]
    app_id: str
    webhook_base_url: str


class ConfigurationError(RuntimeError):
    """Configuration is missing or invalid, and the process must not continue.

    Raised at startup rather than at first use. A process that starts with bad configuration
    fails later, somewhere unrelated, and the traceback points at the wrong thing.
    """


# How SPEECH_VOICES is written, quoted in every error about it so the fix is in the message.
VOICE_CATALOGUE_FORMAT: Final = "id:Display name:locale|locale,id:Display name:locale"


def parse_voice_catalogue(text: str) -> tuple[Voice, ...]:
    """The voices SPEECH_VOICES lists, in the order it lists them.

    A compact string rather than JSON, because this is typed into an environment file by hand and
    JSON quoting inside a shell variable is where a catalogue gets silently truncated. The price is
    that a display name cannot contain a colon or a comma, which no voice name has needed.
    """
    entries = [entry.strip() for entry in text.split(",") if entry.strip()]
    if not entries:
        raise ValueError(f"SPEECH_VOICES lists no voices; expected {VOICE_CATALOGUE_FORMAT!r}")

    voices: list[Voice] = []
    for entry in entries:
        parts = [part.strip() for part in entry.split(":")]
        locales = tuple(locale.strip() for locale in parts[-1].split("|"))
        if len(parts) != 3 or not all(parts) or not all(locales):
            raise ValueError(
                f"SPEECH_VOICES entry {entry!r} is not in the form {VOICE_CATALOGUE_FORMAT!r}"
            )
        voices.append(Voice(id=parts[0], name=parts[1], locales=locales))

    ids = [voice.id for voice in voices]
    repeated = sorted({voice_id for voice_id in ids if ids.count(voice_id) > 1})
    if repeated:
        # Caught here as well as by the provider, so the message names the variable to fix rather
        # than an invariant somebody has to trace back to a line in an environment file.
        raise ValueError(f"SPEECH_VOICES lists the same id more than once: {repeated}")
    return tuple(voices)


# How TRANSCRIPT_ENCRYPTION_KEYS is written, quoted in every error about it.
TRANSCRIPT_KEYS_FORMAT: Final = "newest-id:base64-key,older-id:base64-key"
TRANSCRIPT_KEY_BYTES: Final = 32
_KEY_ID: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}")


def parse_transcript_keys(text: str) -> tuple[tuple[str, bytes], ...]:
    """The transcript keys, newest first, each as its id and its 32 bytes.

    Every error names the entry by position and never repeats what it contains: an entry that
    fails to parse is most often a key pasted without its id, and an error message is copied
    into chat, tickets and logs far more readily than an environment file is.
    """
    entries = [entry.strip() for entry in text.split(",") if entry.strip()]
    if not entries:
        raise ValueError(
            f"TRANSCRIPT_ENCRYPTION_KEYS lists no keys; expected {TRANSCRIPT_KEYS_FORMAT!r}"
        )
    keys: list[tuple[str, bytes]] = []
    for position, entry in enumerate(entries, start=1):
        key_id, separator, encoded = (part.strip() for part in entry.partition(":"))
        if not separator or not _KEY_ID.fullmatch(key_id):
            raise ValueError(
                f"TRANSCRIPT_ENCRYPTION_KEYS entry {position} is not in the form "
                f"{TRANSCRIPT_KEYS_FORMAT!r}; an id is 1-16 lower-case letters, digits, - or _"
            )
        try:
            # Strict: lenient decoding drops characters it does not recognise, so a key damaged
            # in pasting could still come out as thirty-two bytes — just not the ones that
            # sealed anything, which would surface as every transcript failing to open.
            key = base64.b64decode(encoded.replace("-", "+").replace("_", "/"), validate=True)
        except (binascii.Error, ValueError):
            key = b""
        if len(key) != TRANSCRIPT_KEY_BYTES:
            raise ValueError(
                f"TRANSCRIPT_ENCRYPTION_KEYS entry {position} ({key_id!r}) is not "
                f"{TRANSCRIPT_KEY_BYTES} bytes of base64. Generate one with "
                '`python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32))'
                '.decode())"`'
            )
        keys.append((key_id, key))
    ids = [key_id for key_id, _ in keys]
    repeated = sorted({key_id for key_id in ids if ids.count(key_id) > 1})
    if repeated:
        raise ValueError(f"TRANSCRIPT_ENCRYPTION_KEYS uses the same id more than once: {repeated}")
    return tuple(keys)


def _catalogue_from_text(value: object) -> object:
    # Text is what the environment supplies; a tuple of voices is what code constructing
    # settings directly passes, and that needs no parsing.
    return parse_voice_catalogue(value) if isinstance(value, str) else value


def parse_number_list(text: str) -> tuple[PhoneNumber, ...]:
    """The numbers a comma-separated variable lists, each in E.164 form.

    The message names the position of a number it cannot read, never the number: an error
    about configuration is printed where anyone running the process can see it.
    """
    entries = [entry.strip() for entry in text.split(",") if entry.strip()]
    numbers: list[PhoneNumber] = []
    for position, entry in enumerate(entries, 1):
        try:
            numbers.append(PhoneNumber.parse(entry))
        except InvariantError:
            raise ValueError(
                f"TELEPHONY_NUMBERS entry {position} is not an international number in E.164 form"
            ) from None
    if not numbers:
        raise ValueError("TELEPHONY_NUMBERS lists no numbers")
    return tuple(numbers)


def _numbers_from_text(value: object) -> object:
    return parse_number_list(value) if isinstance(value, str) else value


def _sender_from_text(value: object) -> object:
    # The message names the variable and never repeats the value, as every number error here does.
    if not isinstance(value, str):
        return value
    try:
        return PhoneNumber.parse(value)
    except InvariantError:
        raise ValueError("SMS_FROM_NUMBER is not an international number in E.164 form") from None


# How LLM_HEADERS is written, quoted in every error about it.
LLM_HEADERS_FORMAT: Final = "Header-Name=value;Other-Header=value"

# What a header name may be made of (RFC 9110's token), so a typo is refused here rather than by
# the HTTP client on the first call.
_HEADER_NAME: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def parse_llm_headers(text: str) -> tuple[tuple[str, SecretStr], ...]:
    """The extra headers LLM_HEADERS lists, in the order it lists them.

    Compact rather than JSON, for the reason the voice catalogue gives. A value cannot contain a
    semicolon, which the headers aggregators and gateways ask for do not need. Values are secrets:
    a header is as often a second credential as it is a label, and a setting printed in a traceback
    should not decide which.

    The authorisation header is refused. The key already travels in it, and two sources for one
    header is a key that is silently not the one somebody configured.
    """
    headers: list[tuple[str, SecretStr]] = []
    for entry in (entry.strip() for entry in text.split(";")):
        if not entry:
            continue
        name, separator, value = (part.strip() for part in entry.partition("="))
        if not separator or not _HEADER_NAME.fullmatch(name) or not value:
            raise ValueError(
                f"LLM_HEADERS entry for {name or 'a header'!r} is not in the form "
                f"{LLM_HEADERS_FORMAT!r}"
            )
        if name.lower() == "authorization":
            raise ValueError("LLM_HEADERS cannot set Authorization; the key is LLM_API_KEY")
        headers.append((name, SecretStr(value)))

    names = [name.lower() for name, _ in headers]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"LLM_HEADERS names the same header more than once: {repeated}")
    return tuple(headers)


def _headers_from_text(value: object) -> object:
    return parse_llm_headers(value) if isinstance(value, str) else value


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    """Everything needed to reach the model the agent runs on (D-007)."""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: float


def _blank_is_absent(value: object) -> object:
    # `.env.example` lists optional variables with nothing after the equals sign. Copying it must
    # leave them unset, not set to an empty string that then fails as a malformed URL.
    return None if isinstance(value, str) and not value.strip() else value


class Settings(BaseSettings):
    """Everything this application reads from its environment.

    Adding a variable here is the only way to add one. Nothing else in the codebase touches
    ``os.environ``, so this class is also the configuration reference: what it declares is
    what ``.env.example`` documents, and a drift between them is a bug.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        # A validation error otherwise quotes the value it refused, and a value here can be a
        # signing key or an encryption key. The variable's name is what anybody needs to fix it;
        # the message is copied into logs, tickets and chat far more readily than a .env file.
        hide_input_in_errors=True,
    )

    app_env: Environment = Environment.DEVELOPMENT
    log_level: str = "info"
    log_format: LogFormat = LogFormat.CONSOLE

    database_url: Annotated[PostgresDsn | None, BeforeValidator(_blank_is_absent)] = None

    # Authentication. The signing key has no default: a default signing key is a signing key
    # somebody forgets to change, and then anyone who has read this repository can mint a
    # token for any account.
    auth_signing_key: SecretStr | None = None
    auth_access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3600)
    otp_provider: OTPProviderName = OTPProviderName.MOCK
    # The account the text-message code provider sends from, required only when it is chosen.
    # Its own variables rather than the telephony account's: a deployment whose calls arrive on a
    # handset has no telephony account at all and still needs codes delivered, and a credential
    # that can only send texts is revoked without touching the one that carries calls.
    sms_account_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    sms_auth_token: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None
    sms_from_number: Annotated[
        PhoneNumber | None,
        BeforeValidator(_sender_from_text),
        BeforeValidator(_blank_is_absent),
    ] = None

    # Realtime speech. The protocol defaults to the one every existing deployment speaks. The rest
    # is optional at startup: nothing opens a speech session in a request yet, and a process that
    # refuses to start for want of a service it never calls is a process nobody can develop
    # against. The shape is still checked when a value is present, so a typo fails here rather
    # than on the first call. The model names what a realtime service runs; the agent id names
    # which ElevenLabs agent to talk to; each is required only by its own protocol.
    speech_provider: SpeechProviderName = SpeechProviderName.REALTIME
    speech_endpoint_url: Annotated[AnyWebsocketUrl | None, BeforeValidator(_blank_is_absent)] = None
    speech_model: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    speech_agent_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    # Which model transcribes the caller, for a protocol that is told. Optional, and consequential:
    # without it the service answers the caller without writing down what they said, so the
    # conversation's record holds only one side of it.
    speech_transcription_model: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    speech_api_key: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None

    # The voices this deployment offers, and the one a call gets when nobody chose. No default: a
    # compatible server decides its own voices, so any list written here would be a list of voices
    # that some server cannot speak — which is the exact thing that went wrong before this was
    # configuration. `NoDecode` keeps pydantic from reading the text as JSON.
    #
    # Optional here and required by what serves voices, like the database URL: the API refuses to
    # start without them, while a migration, which has no use for a voice, does not.
    speech_voices: Annotated[
        tuple[Voice, ...] | None,
        NoDecode,
        BeforeValidator(_blank_is_absent),
        BeforeValidator(_catalogue_from_text),
    ] = None
    speech_default_voice: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None

    # The keys transcripts are encrypted under, newest first (D-014). Optional at startup, like
    # the database URL: call history cannot be read without them and answers 503 instead, while
    # a migration or the purge, which never read a sealed record, must not need them. Checked
    # for shape whenever present, so a truncated key fails at startup rather than on the first
    # call.
    transcript_encryption_keys: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = (
        None
    )

    # Telephony. All optional at startup, like the speech service. The provider chooses the call
    # transport; the rest is the streaming transport's account, which a deployment without one
    # needs none of, and one with it is refused by what builds the transport, naming every
    # variable that is missing. The token is a secret and is never
    # rendered; the numbers are the ones calls are placed from, never anybody's own.
    telephony_provider: Annotated[
        TelephonyProviderName | None, BeforeValidator(_blank_is_absent)
    ] = None
    telephony_account_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    telephony_auth_token: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None
    telephony_numbers: Annotated[
        tuple[PhoneNumber, ...] | None,
        NoDecode,
        # Validators run last-listed first: a blank is set aside before anything parses it.
        BeforeValidator(_numbers_from_text),
        BeforeValidator(_blank_is_absent),
    ] = None
    telephony_app_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    # The URL the provider reaches this service on, and the one its signatures are computed
    # over. Configured rather than read from a request, because behind a proxy or a tunnel the
    # Host a request arrives with is not the URL the provider signed.
    telephony_webhook_base_url: Annotated[AnyHttpUrl | None, BeforeValidator(_blank_is_absent)] = (
        None
    )

    @field_validator("telephony_webhook_base_url")
    @classmethod
    def _a_base_url_is_only_a_base(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Refuse a query or a fragment: paths are appended to this, and neither survives that."""
        if value is not None and (value.query or value.fragment):
            raise ValueError("TELEPHONY_WEBHOOK_BASE_URL must not carry a query or a fragment")
        return value

    # The model the agent judges calls with: any OpenAI-compatible endpoint (D-007). Optional at
    # startup, like the speech service and for the same reason: nothing in a request asks the agent
    # for a judgement yet. The timeout bounds a whole judgement — every model turn and every tool —
    # because a caller is waiting on the line while it runs.
    llm_base_url: Annotated[AnyHttpUrl | None, BeforeValidator(_blank_is_absent)] = None
    llm_api_key: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None
    llm_model: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    llm_headers: Annotated[
        tuple[tuple[str, SecretStr], ...],
        NoDecode,
        BeforeValidator(_headers_from_text),
    ] = ()
    llm_timeout_seconds: float = Field(default=20, gt=0, le=120)
    # Push notifications for escalations (D-015). Each platform is optional and independent: a
    # deployment with neither still escalates, because the phone ringing is the escalation (D-016)
    # and the app fetches the context when no push arrives. Setting any variable of a platform
    # commits to that platform, and a missing companion stops the process naming it.
    #
    # Keys are given as their content rather than a path. A secret store or a container runtime
    # injects a value, not a file; a path would need a mounted volume as well as a variable, and a
    # second place for the secret to be left behind.
    apns_key_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    apns_team_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    apns_private_key: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None
    apns_topic: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    apns_environment: Annotated[APNsEnvironmentName | None, BeforeValidator(_blank_is_absent)] = (
        None
    )
    fcm_project_id: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    fcm_service_account_json: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None

    @field_validator("log_level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        """Reject a log level that would otherwise silently become something else."""
        allowed = {"debug", "info", "warning", "error", "critical"}
        lowered = value.lower()
        if lowered not in allowed:
            raise ValueError(f"must be one of {', '.join(sorted(allowed))}, got {value!r}")
        return lowered

    @model_validator(mode="after")
    def _production_must_have_a_signing_key(self) -> Settings:
        """Refuse to start in production without one.

        Checked at startup rather than at first use, because the first use is somebody signing
        in: a service that starts and then cannot authenticate anybody is worse than one that
        does not start at all.

        The other production guard — that a mock is not the thing delivering sign-in codes —
        lives in the mock itself. It is the mock's business to refuse, and putting it here
        would mean this file learns something new about every provider ever added.
        """
        if self.app_env is Environment.PRODUCTION and self.auth_signing_key is None:
            raise ValueError("AUTH_SIGNING_KEY is required in production")
        return self

    @model_validator(mode="after")
    def _default_voice_is_in_the_catalogue(self) -> Settings:
        """A default outside the catalogue leaves a call nobody configured with no voice at all.

        Checked whenever either is given, so half a catalogue is refused at startup rather than
        passing here and failing at the first request for voices.
        """
        if self.speech_voices is None and self.speech_default_voice is None:
            return self
        if self.speech_voices is None or self.speech_default_voice is None:
            raise ValueError(
                "SPEECH_VOICES and SPEECH_DEFAULT_VOICE are set together or not at all"
            )
        if self.speech_default_voice not in {voice.id for voice in self.speech_voices}:
            raise ValueError(
                f"SPEECH_DEFAULT_VOICE {self.speech_default_voice!r} is not one of the voices "
                "listed in SPEECH_VOICES"
            )
        return self

    @field_validator("transcript_encryption_keys", mode="after")
    @classmethod
    def _transcript_keys_are_well_formed(cls, value: SecretStr | None) -> SecretStr | None:
        """Check the keys' shape once they are already a `SecretStr`.

        A field validator after the secret is wrapped, rather than a model validator: an error
        raised from the model sees the whole raw input, and an error that carries its input
        carries every key in it.
        """
        if value is not None:
            parse_transcript_keys(value.get_secret_value())
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION

    def require_signing_key(self) -> str:
        """The signing key, or a failure that names what is missing."""
        if self.auth_signing_key is None:
            raise ConfigurationError(
                "AUTH_SIGNING_KEY is required to issue access tokens. Generate one with "
                '`python -c "import secrets; print(secrets.token_urlsafe(48))"`; '
                "see .env.example."
            )
        return self.auth_signing_key.get_secret_value()

    def require_voice_catalogue(self) -> tuple[tuple[Voice, ...], str]:
        """The voices and the default one, or a failure naming the variables to set."""
        if self.speech_voices is None or self.speech_default_voice is None:
            raise ConfigurationError(
                "SPEECH_VOICES and SPEECH_DEFAULT_VOICE are required to offer voices. "
                "Set them in .env; see .env.example."
            )
        return self.speech_voices, self.speech_default_voice

    def require_speech_service(self) -> tuple[str, str]:
        """The speech endpoint and model, or a failure naming whichever is missing.

        Optional at startup because nothing in the running service opens a speech session yet;
        required by whatever does, so that it fails naming the variable rather than connecting to
        nothing.
        """
        return self._require_speech(("SPEECH_MODEL", self.speech_model))

    def require_speech_agent(self) -> tuple[str, str]:
        """The speech endpoint and ElevenLabs agent id, or a failure naming whichever is missing."""
        return self._require_speech(("SPEECH_AGENT_ID", self.speech_agent_id))

    def _require_speech(self, target: tuple[str, str | None]) -> tuple[str, str]:
        endpoint = self.speech_endpoint_url
        name, value = target
        if endpoint is None or value is None:
            missing = [
                each
                for each, present in (("SPEECH_ENDPOINT_URL", endpoint), (name, value))
                if present is None
            ]
            raise ConfigurationError(
                f"{' and '.join(missing)} must be set to hold a spoken conversation with "
                f"SPEECH_PROVIDER={self.speech_provider}. Set them in .env; see .env.example."
            )
        return str(endpoint), value

    def require_transcript_keys(self) -> tuple[tuple[str, bytes], ...]:
        """The transcript keys, newest first, or a failure naming the variable to set."""
        if self.transcript_encryption_keys is None:
            raise ConfigurationError(
                "TRANSCRIPT_ENCRYPTION_KEYS is required to store or read transcripts. "
                f"Set it in .env as {TRANSCRIPT_KEYS_FORMAT!r}; see .env.example."
            )
        return parse_transcript_keys(self.transcript_encryption_keys.get_secret_value())

    def require_sms_account(self) -> SmsAccount:
        """What the text-message code provider needs, or a failure naming every variable missing."""
        account_id, token, sender = self.sms_account_id, self.sms_auth_token, self.sms_from_number
        if account_id is None or token is None or sender is None:
            missing = [
                name
                for name, value in (
                    ("SMS_ACCOUNT_ID", account_id),
                    ("SMS_AUTH_TOKEN", token),
                    ("SMS_FROM_NUMBER", sender),
                )
                if value is None
            ]
            raise ConfigurationError(
                f"{', '.join(missing)} must be set to send sign-in codes with "
                f"OTP_PROVIDER={self.otp_provider}. Set them in .env; see .env.example."
            )
        return SmsAccount(account_id=account_id, auth_token=token.get_secret_value(), sender=sender)

    def require_telephony_configuration(self) -> None:
        """Refuse a chosen call transport that is missing what it needs, before anything starts.

        Only the streaming transport needs an account. A handset transport is configured on the
        handset, and no transport at all needs nothing. Either transport needs storage and the
        transcript keys: calls are owned, recorded and sealed by an orchestrator that is built
        only with them, and a transport with no orchestrator answers callers into a call that
        nothing will ever act on.
        """
        if self.telephony_provider is None:
            return
        if self.telephony_provider is TelephonyProviderName.TWILIO:
            self.require_streaming_telephony()
        missing = [
            name
            for name, value in (
                ("DATABASE_URL", self.database_url),
                ("TRANSCRIPT_ENCRYPTION_KEYS", self.transcript_encryption_keys),
            )
            if value is None
        ]
        if missing:
            raise ConfigurationError(
                f"{', '.join(missing)} must be set to carry calls. "
                "Set them in .env; see .env.example."
            )

    def require_streaming_telephony(self) -> StreamingTelephony:
        """What a streaming call transport needs, or a failure naming every variable missing."""
        account_id = self.telephony_account_id
        token = self.telephony_auth_token
        numbers = self.telephony_numbers
        app_id = self.telephony_app_id
        base_url = self.telephony_webhook_base_url
        if (
            account_id is None
            or token is None
            or numbers is None
            or app_id is None
            or base_url is None
        ):
            missing = [
                name
                for name, value in (
                    ("TELEPHONY_ACCOUNT_ID", account_id),
                    ("TELEPHONY_AUTH_TOKEN", token),
                    ("TELEPHONY_NUMBERS", numbers),
                    ("TELEPHONY_APP_ID", app_id),
                    ("TELEPHONY_WEBHOOK_BASE_URL", base_url),
                )
                if value is None
            ]
            raise ConfigurationError(
                f"{', '.join(missing)} must be set to carry streaming calls. "
                "Set them in .env; see .env.example."
            )
        return StreamingTelephony(
            account_id=account_id,
            auth_token=token.get_secret_value(),
            numbers=numbers,
            app_id=app_id,
            webhook_base_url=str(base_url).rstrip("/"),
        )

    def require_llm(self) -> LLMEndpoint:
        """The agent's model endpoint, or a failure naming whichever variables are missing.

        The key is required even for a server that checks none. Such a server accepts any value;
        leaving it unset would have the model client look for one in its own environment variable,
        which is a second place configuration comes from.
        """
        base_url, api_key, model = self.llm_base_url, self.llm_api_key, self.llm_model
        if base_url is None or api_key is None or model is None:
            missing = [
                name
                for name, present in (
                    ("LLM_BASE_URL", base_url),
                    ("LLM_API_KEY", api_key),
                    ("LLM_MODEL", model),
                )
                if present is None
            ]
            raise ConfigurationError(
                f"{' and '.join(missing)} must be set for the agent to judge a call. "
                "Set them in .env; see .env.example."
            )
        return LLMEndpoint(
            base_url=str(base_url),
            model=model,
            api_key=api_key.get_secret_value(),
            headers={name: value.get_secret_value() for name, value in self.llm_headers},
            timeout_seconds=self.llm_timeout_seconds,
        )

    @property
    def llm_configured(self) -> bool:
        """Whether any model variable is set, which commits the deployment to all of them."""
        return any(
            value is not None for value in (self.llm_base_url, self.llm_api_key, self.llm_model)
        )

    @property
    def apns_configured(self) -> bool:
        """Whether any APNs variable is set, which commits the deployment to all of them."""
        return any(
            value is not None
            for value in (
                self.apns_key_id,
                self.apns_team_id,
                self.apns_private_key,
                self.apns_topic,
                self.apns_environment,
            )
        )

    @property
    def fcm_configured(self) -> bool:
        """Whether any FCM variable is set, which commits the deployment to both."""
        return self.fcm_project_id is not None or self.fcm_service_account_json is not None

    def require_apns(self) -> APNsCredentials:
        """Direct delivery to iOS, or a failure naming every variable still missing."""
        key = self.apns_private_key
        named = (
            ("APNS_KEY_ID", self.apns_key_id),
            ("APNS_TEAM_ID", self.apns_team_id),
            ("APNS_PRIVATE_KEY", key),
            ("APNS_TOPIC", self.apns_topic),
            ("APNS_ENVIRONMENT", self.apns_environment),
        )
        missing = [name for name, value in named if value is None]
        if (
            missing
            or self.apns_key_id is None
            or self.apns_team_id is None
            or key is None
            or self.apns_topic is None
            or self.apns_environment is None
        ):
            raise ConfigurationError(
                f"{', '.join(missing)} must be set to deliver notifications to iOS devices. "
                "Set every APNS_ variable or none; see .env.example."
            )
        return APNsCredentials(
            key_id=self.apns_key_id,
            team_id=self.apns_team_id,
            private_key=key.get_secret_value(),
            topic=self.apns_topic,
            environment=self.apns_environment,
        )

    def require_fcm(self) -> FCMCredentials:
        """Direct delivery to Android, or a failure naming whichever variable is missing."""
        account = self.fcm_service_account_json
        if self.fcm_project_id is None or account is None:
            missing = [
                name
                for name, value in (
                    ("FCM_PROJECT_ID", self.fcm_project_id),
                    ("FCM_SERVICE_ACCOUNT_JSON", account),
                )
                if value is None
            ]
            raise ConfigurationError(
                f"{' and '.join(missing)} must be set to deliver notifications to Android "
                "devices. Set both or neither; see .env.example."
            )
        return FCMCredentials(
            project_id=self.fcm_project_id, service_account_json=account.get_secret_value()
        )

    def require_database_url(self) -> str:
        """The database URL, or a failure that names what is missing.

        Readiness and the session factory need this; liveness does not. Asking for it
        explicitly keeps the optionality visible instead of scattering ``if url is None``.
        """
        if self.database_url is None:
            raise ConfigurationError(
                "DATABASE_URL is required to reach the database. Set it in .env; see .env.example."
            )
        return str(self.database_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate the settings once per process.

    Cached because configuration does not change while a process runs, and because reading it
    repeatedly would make it possible for two parts of the application to disagree about it.
    """
    try:
        return Settings()
    except ValidationError as error:
        variables = ", ".join(
            str(item["loc"][0]).upper() for item in error.errors() if item.get("loc")
        )
        raise ConfigurationError(
            f"configuration is invalid: {variables or 'unknown variable'}. "
            f"See .env.example.\n{error}"
        ) from error
