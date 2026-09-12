"""Fixtures shared by the suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.config.settings import Settings, get_settings
from tests.support.config import REQUIRED_ENVIRONMENT, make_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

# Anything the settings object reads. Cleared for every test so that a variable set on the
# machine running the suite cannot change what the suite proves.
SETTINGS_VARIABLES = (
    "APP_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "SPEECH_PROVIDER",
    "SPEECH_ENDPOINT_URL",
    "SPEECH_MODEL",
    "SPEECH_AGENT_ID",
    "SPEECH_API_KEY",
    *REQUIRED_ENVIRONMENT,
)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    # The same reason, for the file: a developer's own `.env` would otherwise be read by every
    # test that builds settings from the environment, and a test proving the process refuses
    # to start without a catalogue would pass or fail depending on whose machine it ran on.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings for a test: no database, so nothing reaches for one it does not have."""
    return make_settings()


# The database fixtures live in their own module, registered here because pytest only honours
# `pytest_plugins` in the root conftest. Importing it costs nothing: the engine is created
# inside the fixture, so a developer working on the domain never needs PostgreSQL running.
pytest_plugins = ["tests.integration.conftest_db"]
