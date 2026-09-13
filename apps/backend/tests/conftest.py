"""Fixtures shared by the suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.config.settings import Settings, get_settings
from tests.support.config import REQUIRED_ENVIRONMENT, make_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

# Every variable the settings read, cleared for each test so the machine's environment is ignored.
SETTINGS_VARIABLES = (
    "APP_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "SPEECH_PROVIDER",
    "SPEECH_ENDPOINT_URL",
    "SPEECH_MODEL",
    "SPEECH_AGENT_ID",
    "SPEECH_TRANSCRIPTION_MODEL",
    "SPEECH_API_KEY",
    "TRANSCRIPT_ENCRYPTION_KEYS",
    "TELEPHONY_PROVIDER",
    "TELEPHONY_ACCOUNT_ID",
    "TELEPHONY_AUTH_TOKEN",
    "TELEPHONY_NUMBERS",
    "TELEPHONY_APP_ID",
    "TELEPHONY_WEBHOOK_BASE_URL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_HEADERS",
    "LLM_TIMEOUT_SECONDS",
    "APNS_KEY_ID",
    "APNS_TEAM_ID",
    "APNS_PRIVATE_KEY",
    "APNS_TOPIC",
    "APNS_ENVIRONMENT",
    "FCM_PROJECT_ID",
    "FCM_SERVICE_ACCOUNT_JSON",
    *REQUIRED_ENVIRONMENT,
)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    # A developer's own `.env` is ignored too.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings for a test: no database, so nothing reaches for one it does not have."""
    return make_settings()


# The database fixtures, registered here because pytest honours `pytest_plugins` only at the root.
pytest_plugins = ["tests.integration.conftest_db"]
