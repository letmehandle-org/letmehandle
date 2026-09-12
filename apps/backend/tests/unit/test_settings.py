"""Configuration must fail loudly, at startup, naming what is wrong."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from letmehandle.config.settings import (
    ConfigurationError,
    Environment,
    LogFormat,
    Settings,
    get_settings,
)
from tests.support.config import UNREACHABLE_DATABASE, make_settings


def test_defaults_are_development() -> None:
    settings = Settings()
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.log_format is LogFormat.CONSOLE
    assert settings.is_production is False


def test_production_is_recognised() -> None:
    assert Settings(app_env=Environment.PRODUCTION).is_production is True


@pytest.mark.parametrize("level", ["debug", "INFO", "Warning", "error", "critical"])
def test_known_log_levels_are_accepted_and_normalised(level: str) -> None:
    assert Settings(log_level=level).log_level == level.lower()


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        Settings(log_level="chatty")


def test_unknown_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="staging")  # type: ignore[arg-type]


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
        Settings(database_url="not-a-url")  # type: ignore[arg-type]


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_invalid_environment_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid environment must stop the process with a message naming the variable."""
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    get_settings.cache_clear()
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        get_settings()
