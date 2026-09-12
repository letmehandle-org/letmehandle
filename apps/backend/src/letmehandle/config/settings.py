"""The only place this application reads its environment."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import (
    Field,
    PostgresDsn,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    database_url: PostgresDsn | None = None

    # Authentication. The signing key has no default: a default signing key is a signing key
    # somebody forgets to change, and then anyone who has read this repository can mint a
    # token for any account.
    auth_signing_key: SecretStr | None = None
    auth_access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3600)
    otp_provider: OTPProviderName = OTPProviderName.MOCK

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
