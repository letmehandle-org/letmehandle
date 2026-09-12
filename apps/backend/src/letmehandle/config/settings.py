"""The only place this application reads its environment."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final

from pydantic import (
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

from letmehandle.domain.ports.voice import Voice


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


def _catalogue_from_text(value: object) -> object:
    # Text is what the environment supplies; a tuple of voices is what code constructing
    # settings directly passes, and that needs no parsing.
    return parse_voice_catalogue(value) if isinstance(value, str) else value


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

    # Realtime speech. All three optional at startup: nothing opens a speech session in a request
    # yet, and a process that refuses to start for want of a service it never calls is a process
    # nobody can develop against. The shape is still checked when a value is present, so a typo
    # fails here rather than on the first call.
    speech_endpoint_url: Annotated[AnyWebsocketUrl | None, BeforeValidator(_blank_is_absent)] = None
    speech_model: Annotated[str | None, BeforeValidator(_blank_is_absent)] = None
    speech_api_key: Annotated[SecretStr | None, BeforeValidator(_blank_is_absent)] = None

    # The voices this deployment offers, and the one a call gets when nobody chose. Required, with
    # no default: a compatible server decides its own voices, so any list written here would be a
    # list of voices that some server cannot speak — which is the exact thing that went wrong
    # before this was configuration. `NoDecode` keeps pydantic from reading the text as JSON.
    speech_voices: Annotated[tuple[Voice, ...], NoDecode, BeforeValidator(_catalogue_from_text)]
    speech_default_voice: str

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
        """A default outside the catalogue leaves a call nobody configured with no voice at all."""
        if self.speech_default_voice not in {voice.id for voice in self.speech_voices}:
            raise ValueError(
                f"SPEECH_DEFAULT_VOICE {self.speech_default_voice!r} is not one of the voices "
                "listed in SPEECH_VOICES"
            )
        return self

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

    def require_speech_service(self) -> tuple[str, str]:
        """The speech endpoint and model, or a failure naming whichever is missing.

        Optional at startup because nothing in the running service opens a speech session yet;
        required by whatever does, so that it fails naming the variable rather than connecting to
        nothing.
        """
        endpoint, model = self.speech_endpoint_url, self.speech_model
        if endpoint is None or model is None:
            missing = [
                name
                for name, value in (("SPEECH_ENDPOINT_URL", endpoint), ("SPEECH_MODEL", model))
                if value is None
            ]
            raise ConfigurationError(
                f"{' and '.join(missing)} must be set to hold a spoken conversation. "
                "Set them in .env; see .env.example."
            )
        return str(endpoint), model

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
        # The required fields arrive from the environment, which the type checker cannot see.
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        variables = ", ".join(
            str(item["loc"][0]).upper() for item in error.errors() if item.get("loc")
        )
        raise ConfigurationError(
            f"configuration is invalid: {variables or 'unknown variable'}. "
            f"See .env.example.\n{error}"
        ) from error
