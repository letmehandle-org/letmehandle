"""Fixtures shared by the suite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.config.settings import Settings, get_settings
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

# Anything the settings object reads. Cleared for every test so that a variable set on the
# machine running the suite cannot change what the suite proves.
SETTINGS_VARIABLES = ("APP_ENV", "LOG_LEVEL", "LOG_FORMAT", "DATABASE_URL")


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in SETTINGS_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Settings for a test: no database, so nothing reaches for one it does not have."""
    return make_settings()
