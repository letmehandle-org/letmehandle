"""The agent's model endpoint: optional to start, named precisely when it is needed (D-007)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from letmehandle.config.settings import (
    ConfigurationError,
    get_settings,
    parse_llm_headers,
)
from tests.support.config import REQUIRED_ENVIRONMENT, make_settings

ENDPOINT = "http://127.0.0.1:8080/v1"
KEY = "llm-key-that-must-stay-out-of-logs"


@pytest.fixture(autouse=True)
def required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def test_nothing_about_the_model_is_needed_to_start() -> None:
    # A process starts without a model configured.
    settings = get_settings()
    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
    assert settings.llm_model is None
    assert settings.llm_headers == ()
    assert settings.llm_timeout_seconds == 20


def test_a_copied_env_example_leaves_everything_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_HEADERS"):
        monkeypatch.setenv(name, "")
    settings = get_settings()
    assert (settings.llm_base_url, settings.llm_api_key, settings.llm_model) == (None, None, None)
    assert settings.llm_headers == ()


def test_the_endpoint_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", ENDPOINT)
    monkeypatch.setenv("LLM_API_KEY", KEY)
    monkeypatch.setenv("LLM_MODEL", "a-model")
    monkeypatch.setenv("LLM_HEADERS", "X-Title=letmehandle; X-Route = a=b")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "7.5")

    endpoint = get_settings().require_llm()

    assert endpoint.base_url == ENDPOINT
    assert endpoint.model == "a-model"
    assert endpoint.api_key == KEY
    assert endpoint.headers == {"X-Title": "letmehandle", "X-Route": "a=b"}
    assert endpoint.timeout_seconds == 7.5


@pytest.mark.parametrize(
    ("configured", "missing"),
    [
        ({}, "LLM_BASE_URL and LLM_API_KEY and LLM_MODEL must"),
        ({"llm_base_url": ENDPOINT}, "LLM_API_KEY and LLM_MODEL must"),
        ({"llm_base_url": ENDPOINT, "llm_api_key": KEY}, "LLM_MODEL must"),
        ({"llm_api_key": KEY, "llm_model": "a-model"}, "LLM_BASE_URL must"),
    ],
)
def test_asking_for_the_model_names_whatever_is_missing(
    configured: dict[str, str], missing: str
) -> None:
    with pytest.raises(ConfigurationError, match=missing):
        make_settings(**configured).require_llm()  # type: ignore[arg-type]


def test_neither_the_key_nor_a_header_is_rendered_by_accident() -> None:
    settings = make_settings(
        llm_base_url=ENDPOINT,
        llm_api_key=KEY,
        llm_model="a-model",
        llm_headers="X-Gateway-Key=gateway-secret",
    )
    for rendered in (repr(settings), repr(settings.require_llm())):
        assert KEY not in rendered
        assert "gateway-secret" not in rendered


@pytest.mark.parametrize("seconds", ["0", "-1", "121", "soon"])
def test_a_timeout_outside_what_a_waiting_caller_allows_is_refused(
    monkeypatch: pytest.MonkeyPatch, seconds: str
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", seconds)
    with pytest.raises(ConfigurationError, match="LLM_TIMEOUT_SECONDS"):
        get_settings()


class TestHeaders:
    def test_entries_are_read_in_order_with_their_values_kept_secret(self) -> None:
        headers = parse_llm_headers(" X-One=1 ;; X-Two=two words ;")
        assert [(name, value.get_secret_value()) for name, value in headers] == [
            ("X-One", "1"),
            ("X-Two", "two words"),
        ]
        assert all(isinstance(value, SecretStr) for _, value in headers)

    @pytest.mark.parametrize("text", ["X-One", "X-One=", "=value", "Not a name=value"])
    def test_a_malformed_entry_is_named_with_the_format_it_should_have(self, text: str) -> None:
        with pytest.raises(ValueError, match="is not in the form 'Header-Name=value"):
            parse_llm_headers(text)

    def test_the_authorisation_header_is_refused(self) -> None:
        # The key already travels in it, so a second source is refused.
        with pytest.raises(ValueError, match="the key is LLM_API_KEY"):
            parse_llm_headers("authorization=Bearer something")

    def test_a_header_named_twice_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"more than once: \['x-one'\]"):
            parse_llm_headers("X-One=1;x-one=2")

    def test_a_malformed_header_stops_the_process_at_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_HEADERS", "no-equals-sign")
        with pytest.raises(ConfigurationError, match="LLM_HEADERS"):
            get_settings()
