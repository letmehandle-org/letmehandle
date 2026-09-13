"""The only place this application reads its environment."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache, partial
from ipaddress import IPv4Network, IPv6Network, ip_network
from typing import TYPE_CHECKING, Annotated, Final

from pydantic import (
    AfterValidator,
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

from letmehandle.config.listing import entries, is_calling_code, repeated
from letmehandle.config.telephony_lines import (
    LineDescription,
    LineProviderName,
    TelephonyLine,
    parse_line_tokens,
    parse_telephony_lines,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.voice import Voice

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


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
    TWILIO_VERIFY = "twilio_verify"


class SpeechProviderName(StrEnum):
    """Which protocol the speech service speaks, and so which adapter talks to it."""

    REALTIME = "realtime"
    ELEVENLABS = "elevenlabs"
    GPT_LIVE = "gpt_live"


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
class VerifyAccount:
    """Everything the verification-service code provider needs, present and checked."""

    account_id: str
    auth_token: str = field(repr=False)
    service_id: str


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
    listed = entries(text)
    if not listed:
        raise ValueError(f"SPEECH_VOICES lists no voices; expected {VOICE_CATALOGUE_FORMAT!r}")

    voices: list[Voice] = []
    for entry in listed:
        parts = [part.strip() for part in entry.split(":")]
        locales = tuple(locale.strip() for locale in parts[-1].split("|"))
        if len(parts) != 3 or not all(parts) or not all(locales):
            raise ValueError(
                f"SPEECH_VOICES entry {entry!r} is not in the form {VOICE_CATALOGUE_FORMAT!r}"
            )
        voices.append(Voice(id=parts[0], name=parts[1], locales=locales))

    repeated_ids = repeated(voice.id for voice in voices)
    if repeated_ids:
        # Caught here as well as by the provider, so the message names the variable to fix rather
        # than an invariant somebody has to trace back to a line in an environment file.
        raise ValueError(f"SPEECH_VOICES lists the same id more than once: {repeated_ids}")
    return tuple(voices)


# A language code as a speech service names one: `en`, `hi`, or a regional `pt-BR`.
_LANGUAGE_CODE: Final = re.compile(r"[a-z]{2,3}(-[A-Za-z0-9]{2,8})?")


def parse_speech_languages(text: str) -> tuple[str, ...]:
    """The languages SPEECH_LANGUAGES lists, in its order: `en,hi`."""
    languages = entries(text)
    if not languages:
        raise ValueError("SPEECH_LANGUAGES lists no languages; expected codes such as 'en,hi'")
    for position, language in enumerate(languages, 1):
        if not _LANGUAGE_CODE.fullmatch(language):
            raise ValueError(
                f"SPEECH_LANGUAGES entry {position} is not a language code, such as en or hi"
            )
    if repeated(languages):
        raise ValueError("SPEECH_LANGUAGES lists the same language more than once")
    return tuple(languages)


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
    listed = entries(text)
    if not listed:
        raise ValueError(
            f"TRANSCRIPT_ENCRYPTION_KEYS lists no keys; expected {TRANSCRIPT_KEYS_FORMAT!r}"
        )
    keys: list[tuple[str, bytes]] = []
    for position, entry in enumerate(listed, start=1):
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
    repeated_ids = repeated(key_id for key_id, _ in keys)
    if repeated_ids:
        raise ValueError(
            f"TRANSCRIPT_ENCRYPTION_KEYS uses the same id more than once: {repeated_ids}"
        )
    return tuple(keys)


def parse_number_list(text: str) -> tuple[PhoneNumber, ...]:
    """The numbers a comma-separated variable lists, each in E.164 form.

    The message names the position of a number it cannot read, never the number: an error
    about configuration is printed where anyone running the process can see it.
    """
    numbers = tuple(
        _number(f"TELEPHONY_NUMBERS entry {position}", entry)
        for position, entry in enumerate(entries(text), 1)
    )
    if not numbers:
        raise ValueError("TELEPHONY_NUMBERS lists no numbers")
    return numbers


def _number(what: str, text: str) -> PhoneNumber:
    """The number `text` is in E.164 form, or an error naming `what` and never the text."""
    try:
        return PhoneNumber.parse(text)
    except InvariantError:
        raise ValueError(f"{what} is not an international number in E.164 form") from None


def _parsed(parse: Callable[[str], object]) -> BeforeValidator:
    """A validator that parses text with `parse` and passes any other value through."""
    return BeforeValidator(lambda value: parse(value) if isinstance(value, str) else value)


def _e164(variable: str) -> BeforeValidator:
    """A validator that reads one number in E.164 form, naming `variable` when it cannot."""
    return _parsed(partial(_number, variable))


def parse_calling_codes(text: str) -> frozenset[str] | None:
    """Country calling codes, comma-separated and without the plus: "91,1,44". Blank is any."""
    codes = [entry.lstrip("+") for entry in entries(text)]
    if not codes:
        return None
    for position, code in enumerate(codes, 1):
        if not is_calling_code(code):
            raise ValueError(
                f"OTP_ALLOWED_CALLING_CODES entry {position} is not a country calling code, "
                "such as 91 or 1"
            )
    return frozenset(codes)


# How OTP_PROVIDER_BY_CALLING_CODE is written, quoted in every error about it.
OTP_PROVIDERS_FORMAT: Final = "91:twilio_verify,1:mock"


def parse_otp_providers(text: str) -> tuple[tuple[str, OTPProviderName], ...]:
    """Which provider sends codes to each calling code, as `code:provider`, comma-separated."""
    routes: dict[str, OTPProviderName] = {}
    known = {provider.value for provider in OTPProviderName}
    for position, entry in enumerate(entries(text), 1):
        code, separator, name = (part.strip() for part in entry.partition(":"))
        code = code.lstrip("+")
        if not separator or not is_calling_code(code) or name not in known:
            raise ValueError(
                f"OTP_PROVIDER_BY_CALLING_CODE entry {position} is not a calling code and one of "
                f"{', '.join(sorted(known))}, as in {OTP_PROVIDERS_FORMAT!r}"
            )
        if code in routes:
            raise ValueError(f"OTP_PROVIDER_BY_CALLING_CODE names calling code {code} twice")
        routes[code] = OTPProviderName(name)
    return tuple(routes.items())


def parse_proxy_networks(text: str) -> tuple[IPv4Network | IPv6Network, ...]:
    """The networks whose forwarding headers are believed, comma-separated CIDRs. Blank is none."""
    networks: list[IPv4Network | IPv6Network] = []
    for position, entry in enumerate(entries(text), 1):
        try:
            networks.append(ip_network(entry, strict=False))
        except ValueError:
            raise ValueError(
                f"TRUSTED_PROXY_CIDRS entry {position} is not a network, such as 10.0.0.0/8"
            ) from None
    return tuple(networks)


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
    for entry in entries(text, ";"):
        name, separator, value = (part.strip() for part in entry.partition("="))
        if not separator or not _HEADER_NAME.fullmatch(name) or not value:
            raise ValueError(
                f"LLM_HEADERS entry for {name or 'a header'!r} is not in the form "
                f"{LLM_HEADERS_FORMAT!r}"
            )
        if name.lower() == "authorization":
            raise ValueError("LLM_HEADERS cannot set Authorization; the key is LLM_API_KEY")
        headers.append((name, SecretStr(value)))

    repeated_names = repeated(name.lower() for name, _ in headers)
    if repeated_names:
        raise ValueError(f"LLM_HEADERS names the same header more than once: {repeated_names}")
    return tuple(headers)


@dataclass(frozen=True, slots=True)
class LLMEndpoint:
    """Everything needed to reach the model the agent runs on (D-007)."""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    timeout_seconds: float


# The fewest characters a signing key or a bearer token may have.
MIN_SECRET_LENGTH: Final = 32


def _at_least_min_length(variable: str) -> AfterValidator:
    """Refuse a secret shorter than `MIN_SECRET_LENGTH`, naming `variable` and never the value."""

    def check(value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_SECRET_LENGTH:
            raise ValueError(f"{variable} must be at least {MIN_SECRET_LENGTH} characters")
        return value

    return AfterValidator(check)


# When a group of variables is required, named once so the generated reference says it one way.
_STREAMING_CALLS: Final = "`TELEPHONY_PROVIDER=twilio`"
_LINES: Final = "`TELEPHONY_LINES` is set"
_MODEL: Final = "the agent judges calls or a model writes summaries; all three together"
_APNS: Final = "any `APNS_` variable is set"
_FCM: Final = "any `FCM_` variable is set"


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

    app_env: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Where the process runs. Production refuses what must never run there, such "
        "as the mock sign-in provider.",
    )
    log_level: str = Field(default="info", description="How much is logged.")
    log_format: LogFormat = Field(
        default=LogFormat.CONSOLE, description="`json` in production, `console` in development."
    )

    database_url: Annotated[
        PostgresDsn | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The PostgreSQL database, as `postgresql+asyncpg://user:password@host/db`.",
            json_schema_extra={"required_when": "anything is stored: sign-in, calls, migrations"},
        ),
    ] = None

    # Authentication. The signing key has no default: a default signing key is a signing key
    # somebody forgets to change, and then anyone who has read this repository can mint a
    # token for any account.
    auth_signing_key: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        _at_least_min_length("AUTH_SIGNING_KEY"),
        Field(
            description="Signs access tokens and keys the refresh-token hash. At least 32 "
            "characters, fresh for every deployment; changing it signs everybody out.",
            json_schema_extra={"required_when": "the API starts"},
        ),
    ] = None
    auth_access_token_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description="How long an access token lives. Short, because it cannot be revoked.",
    )
    auth_refresh_token_ttl_seconds: int = Field(
        default=7_776_000,
        ge=3600,
        description="How long a refresh token lives: ninety days, sliding, so a phone that opens "
        "the app within that long of the last time is never asked for its number again. Refresh "
        "tokens rotate on every use.",
    )
    otp_provider: OTPProviderName = Field(
        default=OTPProviderName.MOCK,
        description="Who delivers sign-in codes. `mock` delivers nowhere, accepts the development "
        "code, and refuses to start in production. `twilio_sms` texts a code the application "
        "makes; `twilio_verify` has the verification service make, text and check its own "
        "(D-042).",
    )
    # Who delivers codes to particular countries, where the default provider should not: a country
    # whose operators accept messages only from a sender registered with a provider licensed there.
    otp_provider_by_calling_code: Annotated[
        tuple[tuple[str, OTPProviderName], ...],
        NoDecode,
        _parsed(parse_otp_providers),
        Field(
            description="Who delivers sign-in codes to numbers with particular calling codes, as "
            "`code:provider`, comma-separated, such as `91:twilio_verify`. Every other number is "
            "sent its code by `OTP_PROVIDER` (D-041).",
        ),
    ] = ()
    otp_allowed_calling_codes: Annotated[
        frozenset[str] | None,
        NoDecode,
        _parsed(parse_calling_codes),
        Field(
            description="Country calling codes sign-in codes may be sent to, comma-separated "
            "without the plus, such as 91,1,44. Blank sends anywhere; a production deployment "
            "should list only the countries it serves (D-036).",
        ),
    ] = None
    otp_challenges_per_hour: int | None = Field(
        default=500,
        ge=1,
        description="The most sign-in codes the deployment sends in an hour. Past it, codes stop "
        "for everybody until the hour rolls on (D-036).",
    )
    otp_challenges_per_hour_per_calling_code: int | None = Field(
        default=100,
        ge=1,
        description="The most sign-in codes sent in an hour to numbers with any one calling code.",
    )
    trusted_proxy_cidrs: Annotated[
        tuple[IPv4Network | IPv6Network, ...],
        NoDecode,
        _parsed(parse_proxy_networks),
        Field(
            description="The proxies in front of the backend, as comma-separated CIDRs. Only "
            "their X-Forwarded-For is believed when counting what one client asks for.",
        ),
    ] = ()
    # The account the text-message code provider sends from, required only when it is chosen.
    # Its own variables rather than the telephony account's: a deployment whose calls arrive on a
    # handset has no telephony account at all and still needs codes delivered, and a credential
    # that can only send texts is revoked without touching the one that carries calls.
    sms_account_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The account sign-in texts are sent from.",
            json_schema_extra={"required_when": "OTP_PROVIDER is twilio_sms or twilio_verify"},
        ),
    ] = None
    sms_auth_token: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="That account's auth token; anyone holding it can send texts on it.",
            json_schema_extra={"required_when": "OTP_PROVIDER is twilio_sms or twilio_verify"},
        ),
    ] = None
    sms_from_number: Annotated[
        PhoneNumber | None,
        _e164("SMS_FROM_NUMBER"),
        BeforeValidator(_blank_is_absent),
        Field(
            description="The number sign-in texts come from, in E.164 form.",
            json_schema_extra={"required_when": "OTP_PROVIDER is twilio_sms"},
        ),
    ] = None
    # The verification service on that same account, for the provider whose service makes, texts
    # and checks the code. An identifier rather than a secret: the account's token is the secret.
    sms_verify_service_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The verification service, on the `SMS_` account, that makes, texts and "
            "checks sign-in codes.",
            json_schema_extra={"required_when": "OTP_PROVIDER is twilio_verify"},
        ),
    ] = None

    # Realtime speech. The protocol defaults to the one every existing deployment speaks. The rest
    # is optional at startup: nothing opens a speech session in a request yet, and a process that
    # refuses to start for want of a service it never calls is a process nobody can develop
    # against. The shape is still checked when a value is present, so a typo fails here rather
    # than on the first call. The model names what a realtime or GPT-Live service runs; the agent
    # id names which ElevenLabs agent to talk to; each is required only by its own protocol.
    speech_provider: SpeechProviderName = Field(
        default=SpeechProviderName.REALTIME,
        description="Which protocol the speech service speaks.",
    )
    # What the service speaks, which is the service's configuration: an ElevenLabs agent's languages
    # are its own and its presets, a realtime model's are the model's. A call opens in the user's
    # language when it is listed here, and the service is asked for nothing it does not list.
    speech_languages: Annotated[
        tuple[str, ...],
        NoDecode,
        _parsed(parse_speech_languages),
        Field(
            description="The languages the speech service speaks, comma-separated, such as "
            "`en,hi`. A call opens in the user's language when it is one of them (D-039).",
        ),
    ] = ("en",)
    speech_endpoint_url: Annotated[
        AnyWebsocketUrl | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The speech service's `ws://` or `wss://` URL.",
            json_schema_extra={"required_when": "the assistant takes calls"},
        ),
    ] = None
    speech_model: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The model a `realtime` or `gpt_live` service runs.",
            json_schema_extra={
                "required_when": "the assistant takes calls over `realtime` or `gpt_live`"
            },
        ),
    ] = None
    speech_agent_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The agent an `elevenlabs` service talks as.",
            json_schema_extra={"required_when": "the assistant takes calls over `elevenlabs`"},
        ),
    ] = None
    # Which model transcribes the caller, for a protocol that is told. Optional, and consequential:
    # without it the service answers the caller without writing down what they said, so the
    # conversation's record holds only one side of it.
    speech_transcription_model: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="`realtime` only: the model that writes down what the caller says. Without "
            "it only the assistant's side of a call is recorded."
        ),
    ] = None
    speech_api_key: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(description="The speech service's key. Empty for a service that needs none."),
    ] = None

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
        _parsed(parse_voice_catalogue),
        Field(
            description="The voices offered, as `id:Display name:locale|locale`, comma-separated, "
            "such as `voice-a:An English voice:en,voice-b:A Hindi voice:hi`. They must be voices "
            "the speech service can speak; a call is spoken in a voice listed for its language.",
            json_schema_extra={"required_when": "the API starts"},
        ),
    ] = None
    speech_default_voice: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="One of the ids in `SPEECH_VOICES`, for a call whose user chose none.",
            json_schema_extra={"required_when": "the API starts"},
        ),
    ] = None

    # The keys transcripts are encrypted under, newest first (D-014). Optional at startup, like
    # the database URL: call history cannot be read without them and answers 503 instead, while
    # a migration or the purge, which never read a sealed record, must not need them. Checked
    # for shape whenever present, so a truncated key fails at startup rather than on the first
    # call.
    transcript_encryption_keys: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="AES-256-GCM keys for transcripts and summaries, as "
            "`id:base64-key` pairs, comma-separated, newest first.",
            json_schema_extra={"required_when": "call history is read or calls are carried"},
        ),
    ] = None

    # Telephony. All optional at startup, like the speech service. The provider chooses the call
    # transport; the rest is the streaming transport's account, which a deployment without one
    # needs none of, and one with it is refused by what builds the transport, naming every
    # variable that is missing. The token is a secret and is never
    # rendered; the numbers are the ones calls are placed from, never anybody's own.
    telephony_provider: Annotated[
        TelephonyProviderName | None,
        BeforeValidator(_blank_is_absent),
        Field(description="Which call transport carries calls. Empty carries none."),
    ] = None
    telephony_account_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The telephony account the REST API authenticates as.",
            json_schema_extra={"required_when": _STREAMING_CALLS},
        ),
    ] = None
    telephony_auth_token: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The account's auth token, which signs every callback.",
            json_schema_extra={"required_when": _STREAMING_CALLS},
        ),
    ] = None
    telephony_numbers: Annotated[
        tuple[PhoneNumber, ...] | None,
        NoDecode,
        # Validators run last-listed first: a blank is set aside before anything parses it.
        _parsed(parse_number_list),
        BeforeValidator(_blank_is_absent),
        Field(
            description="The numbers calls are placed from, comma-separated, in E.164 form.",
            json_schema_extra={"required_when": _STREAMING_CALLS},
        ),
    ] = None
    telephony_app_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The provider-side application the assistant joins each call through.",
            json_schema_extra={"required_when": _STREAMING_CALLS},
        ),
    ] = None
    # The URL the provider reaches this service on, and the one its signatures are computed
    # over. Configured rather than read from a request, because behind a proxy or a tunnel the
    # Host a request arrives with is not the URL the provider signed.
    telephony_webhook_base_url: Annotated[
        AnyHttpUrl | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The public base URL the provider calls back on, exactly as configured "
            "there. Signatures are checked against it.",
            json_schema_extra={"required_when": _STREAMING_CALLS},
        ),
    ] = None

    # Lines by region: the alternative to the single account above, for a deployment whose users are
    # in more than one country and should each forward to, and be rung from, a local number. The
    # structure and the tokens are two variables, so the one holding secrets is never the one
    # somebody reads out to find which line is misconfigured.
    telephony_lines: Annotated[
        tuple[LineDescription, ...] | None,
        NoDecode,
        _parsed(parse_telephony_lines),
        BeforeValidator(_blank_is_absent),
        Field(
            description="Telephony lines by region, instead of `TELEPHONY_PROVIDER` and its "
            "account: `name:provider=twilio;regions=US|IN;numbers=+E164|+E164;account=id;app=id;"
            "webhook=https://host`, comma-separated. `regions=*` serves every region no other "
            "line does. A line's callbacks are under `/lines/<name>` (D-041).",
        ),
    ] = None
    telephony_line_auth_tokens: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="Each line's auth token, as `name:token`, comma-separated.",
            json_schema_extra={"required_when": _LINES},
        ),
    ] = None

    # Whose a call dialled straight at the account's number is, for trying a deployment from a
    # phone without setting up forwarding. Development only: in production such a call belongs to
    # nobody, because anybody can dial the number and must not reach a user's assistant by it.
    telephony_unforwarded_calls_owner: Annotated[
        PhoneNumber | None,
        _e164("TELEPHONY_UNFORWARDED_CALLS_OWNER"),
        BeforeValidator(_blank_is_absent),
        Field(
            description="Development only: the signed-in number whose calls dialled straight at "
            "the account's number are. Refused in production.",
        ),
    ] = None

    # How long a call may last before its run ends it as failed. Generous, because a long call is a
    # real call; bounded, because a call whose ending is never reported is otherwise held for as
    # long as the process runs. Between a minute and a day.
    call_max_duration_seconds: int = Field(
        default=14_400,
        ge=60,
        le=86_400,
        description="How long a call may last before it is ended as failed: generous, because a "
        "long call is a real call, and bounded, because an ending never reported is otherwise "
        "held for as long as the process runs.",
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
    llm_base_url: Annotated[
        AnyHttpUrl | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The OpenAI-compatible endpoint the agent and the summariser use.",
            json_schema_extra={"required_when": _MODEL},
        ),
    ] = None
    llm_api_key: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The endpoint's key. Any value for a server that checks none.",
            json_schema_extra={"required_when": _MODEL},
        ),
    ] = None
    llm_model: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(description="The model to ask.", json_schema_extra={"required_when": _MODEL}),
    ] = None
    llm_headers: Annotated[
        tuple[tuple[str, SecretStr], ...],
        NoDecode,
        _parsed(parse_llm_headers),
        Field(
            description="Extra headers some gateways ask for, as "
            "`Header-Name=value;Other-Header=value`. Cannot set `Authorization`."
        ),
    ] = ()
    llm_timeout_seconds: float = Field(
        default=20,
        gt=0,
        le=120,
        description="How long one judgement may take, in seconds, every model turn and tool "
        "included.",
    )
    # Push notifications for escalations (D-015). Each platform is optional and independent: a
    # deployment with neither still escalates, because the phone ringing is the escalation (D-016)
    # and the app fetches the context when no push arrives. Setting any variable of a platform
    # commits to that platform, and a missing companion stops the process naming it.
    #
    # Keys are given as their content rather than a path. A secret store or a container runtime
    # injects a value, not a file; a path would need a mounted volume as well as a variable, and a
    # second place for the secret to be left behind.
    apns_key_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The 10-character id of the push token-signing key.",
            json_schema_extra={"required_when": _APNS},
        ),
    ] = None
    apns_team_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The 10-character team id of the developer account.",
            json_schema_extra={"required_when": _APNS},
        ),
    ] = None
    apns_private_key: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The .p8 key's content on one line, each newline written as `\\n`.",
            json_schema_extra={"required_when": _APNS},
        ),
    ] = None
    apns_topic: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The app's bundle identifier, which notifications are sent to.",
            json_schema_extra={"required_when": _APNS},
        ),
    ] = None
    apns_environment: Annotated[
        APNsEnvironmentName | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="`sandbox` for development builds, `production` for distributed ones.",
            json_schema_extra={"required_when": _APNS},
        ),
    ] = None
    fcm_project_id: Annotated[
        str | None,
        BeforeValidator(_blank_is_absent),
        Field(description="The Firebase project id.", json_schema_extra={"required_when": _FCM}),
    ] = None
    fcm_service_account_json: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        Field(
            description="The service account's JSON key, on one line, allowed to send messages.",
            json_schema_extra={"required_when": _FCM},
        ),
    ] = None

    # Observability. Spans are exported over OTLP's HTTP protocol to this URL, a collector's traces
    # endpoint, and to nowhere without one: a deployment with no tracing backend runs with none and
    # loses nothing else. The URL is the collector's, never a credential; one that needs a key is
    # reached through a collector beside the service rather than configured here.
    tracing_otlp_endpoint: Annotated[
        AnyHttpUrl | None,
        BeforeValidator(_blank_is_absent),
        Field(description="An OTLP/HTTP collector spans are exported to. Blank exports none."),
    ] = None
    # The bearer token the diagnostics routes require. Without one they do not exist: they list live
    # calls and every provider's latency, which no signed-in user of the app is owed.
    diagnostics_token: Annotated[
        SecretStr | None,
        BeforeValidator(_blank_is_absent),
        _at_least_min_length("DIAGNOSTICS_TOKEN"),
        Field(
            description="The bearer token the diagnostics routes require, at least 32 characters. "
            "Blank leaves those routes unmounted.",
        ),
    ] = None

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
    def _codes_are_routed_only_where_they_may_be_sent(self) -> Settings:
        """Refuse a provider for a calling code sign-in codes are never sent to.

        It could never be used, and a deployment that lists one most likely meant to allow the
        country too and will find its users there refused at sign-in.
        """
        allowed = self.otp_allowed_calling_codes
        unsent = sorted(
            code for code, _ in self.otp_provider_by_calling_code if allowed and code not in allowed
        )
        if unsent:
            raise ValueError(
                f"OTP_PROVIDER_BY_CALLING_CODE names {', '.join(unsent)}, which "
                "OTP_ALLOWED_CALLING_CODES does not allow codes to be sent to"
            )
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

    @field_validator("telephony_line_auth_tokens", mode="after")
    @classmethod
    def _line_tokens_are_well_formed(cls, value: SecretStr | None) -> SecretStr | None:
        """Check the tokens' shape once they are a `SecretStr`, as the transcript keys are."""
        if value is not None:
            parse_line_tokens(value.get_secret_value())
        return value

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

    def require_speech_live_model(self) -> tuple[str, str]:
        """The speech endpoint and GPT-Live model, or a failure naming whichever is missing."""
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
                f"{OTPProviderName.TWILIO_SMS}. Set them in .env; see .env.example."
            )
        return SmsAccount(account_id=account_id, auth_token=token.get_secret_value(), sender=sender)

    def require_verify_account(self) -> VerifyAccount:
        """What the verification-service provider needs, or a failure naming whatever is missing."""
        account_id, token = self.sms_account_id, self.sms_auth_token
        service_id = self.sms_verify_service_id
        if account_id is None or token is None or service_id is None:
            missing = [
                name
                for name, value in (
                    ("SMS_ACCOUNT_ID", account_id),
                    ("SMS_AUTH_TOKEN", token),
                    ("SMS_VERIFY_SERVICE_ID", service_id),
                )
                if value is None
            ]
            raise ConfigurationError(
                f"{', '.join(missing)} must be set to send sign-in codes with "
                f"{OTPProviderName.TWILIO_VERIFY}. Set them in .env; see .env.example."
            )
        return VerifyAccount(
            account_id=account_id, auth_token=token.get_secret_value(), service_id=service_id
        )

    def require_telephony_configuration(self) -> None:
        """Refuse a chosen call transport that is missing what it needs, before anything starts.

        Only streaming lines need an account. A handset transport is configured on the handset,
        and no transport at all needs nothing. Either needs storage and the transcript keys: calls
        are owned, recorded and sealed by an orchestrator that is built only with them, and a
        transport with no orchestrator answers callers into a call that nothing will ever act on.
        """
        if self.is_production and self.telephony_unforwarded_calls_owner is not None:
            raise ConfigurationError(
                "TELEPHONY_UNFORWARDED_CALLS_OWNER is for trying a deployment and is refused in "
                "production, where anybody could dial the number and reach that user's assistant."
            )
        if self.telephony_provider is None and self.telephony_lines is None:
            return
        self.require_telephony_lines()
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

    def require_telephony_lines(self) -> tuple[TelephonyLine, ...]:
        """The streaming lines calls arrive on, or a failure naming what is missing or at odds.

        None where calls arrive on a handset or not at all. `TELEPHONY_PROVIDER=twilio` is one line
        serving every region, from the `TELEPHONY_` account variables; `TELEPHONY_LINES` is lines by
        region, and the two are refused together, because which of them a deployment meant is a
        guess that decides where every user forwards their calls.
        """
        if self.telephony_lines is not None:
            return self._lines_by_region(self.telephony_lines)
        if self.telephony_provider is TelephonyProviderName.TWILIO:
            return (self._the_one_line(),)
        return ()

    def _lines_by_region(self, lines: tuple[LineDescription, ...]) -> tuple[TelephonyLine, ...]:
        single = [
            name
            for name, value in (
                ("TELEPHONY_PROVIDER", self.telephony_provider),
                ("TELEPHONY_ACCOUNT_ID", self.telephony_account_id),
                ("TELEPHONY_AUTH_TOKEN", self.telephony_auth_token),
                ("TELEPHONY_NUMBERS", self.telephony_numbers),
                ("TELEPHONY_APP_ID", self.telephony_app_id),
                ("TELEPHONY_WEBHOOK_BASE_URL", self.telephony_webhook_base_url),
            )
            if value is not None
        ]
        if single:
            raise ConfigurationError(
                f"{', '.join(single)} configure a single line and cannot be set with "
                "TELEPHONY_LINES. Describe every line in TELEPHONY_LINES and unset them; see "
                ".env.example."
            )
        if self.telephony_line_auth_tokens is None:
            raise ConfigurationError(
                "TELEPHONY_LINE_AUTH_TOKENS must be set to carry calls on TELEPHONY_LINES. "
                "Set it in .env; see .env.example."
            )
        tokens = parse_line_tokens(self.telephony_line_auth_tokens.get_secret_value())
        names = {line.name for line in lines}
        untokened = sorted(names - set(tokens))
        unknown = sorted(set(tokens) - names)
        if untokened or unknown:
            raise ConfigurationError(
                "TELEPHONY_LINE_AUTH_TOKENS must give a token for every line in TELEPHONY_LINES "
                f"and no other: missing {untokened or 'none'}, unknown {unknown or 'none'}."
            )
        return tuple(line.with_token(tokens[line.name]) for line in lines)

    def _the_one_line(self) -> TelephonyLine:
        """The line `TELEPHONY_PROVIDER=twilio` configures, or a failure naming what is missing."""
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
        return TelephonyLine(
            name=None,
            provider=LineProviderName.TWILIO,
            regions=None,
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
