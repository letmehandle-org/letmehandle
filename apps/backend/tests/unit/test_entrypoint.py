"""The entry point must refuse to start on bad configuration, and say why."""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING

import pytest

from letmehandle.config.settings import get_settings
from letmehandle.main import main
from tests.support.config import REQUIRED_ENVIRONMENT

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def recorded_uvicorn(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, object]]:
    """Stand in for uvicorn so that the entry point can be run without binding a port."""
    recorded: dict[str, object] = {}

    def run(target: str, **kwargs: object) -> None:
        recorded["target"] = target
        recorded.update(kwargs)

    module = types.ModuleType("uvicorn")
    module.run = run  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "uvicorn", module)
    yield recorded


def test_main_starts_the_server_with_the_application_factory(
    monkeypatch: pytest.MonkeyPatch, recorded_uvicorn: dict[str, object]
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    main()
    assert recorded_uvicorn["target"] == "letmehandle.main:create_app"
    assert recorded_uvicorn["factory"] is True
    assert recorded_uvicorn["port"] == 8000
    # structlog owns logging; uvicorn must not emit a second account of every request.
    assert recorded_uvicorn["log_config"] is None
    assert recorded_uvicorn["access_log"] is False


def test_main_refuses_to_start_on_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, recorded_uvicorn: dict[str, object]
) -> None:
    """A bad variable must be one clear line, not a traceback from inside a worker."""
    monkeypatch.setenv("LOG_LEVEL", "chatty")
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert "LOG_LEVEL" in str(exit_info.value)
    assert recorded_uvicorn == {}


def test_main_refuses_to_start_without_a_voice_catalogue(
    recorded_uvicorn: dict[str, object],
) -> None:
    """Nothing is set, so the one variable with no possible default is the one named."""
    with pytest.raises(SystemExit) as exit_info:
        main()

    assert "SPEECH_VOICES" in str(exit_info.value)
    assert recorded_uvicorn == {}
