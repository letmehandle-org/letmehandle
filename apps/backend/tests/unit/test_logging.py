"""Every line must be able to say which request produced it."""

from __future__ import annotations

import logging

import structlog

from letmehandle.config.settings import LogFormat
from letmehandle.observability.logging import (
    add_correlation_id,
    configure_logging,
    correlation_id,
    get_logger,
)
from tests.support.config import make_settings


def test_correlation_id_is_absent_when_unset() -> None:
    correlation_id.set(None)
    assert add_correlation_id(None, "info", {"event": "x"}) == {"event": "x"}


def test_correlation_id_is_attached_when_set() -> None:
    token = correlation_id.set("abc-123")
    try:
        assert add_correlation_id(None, "info", {"event": "x"})["correlation_id"] == "abc-123"
    finally:
        correlation_id.reset(token)


def test_json_format_is_configured() -> None:
    configure_logging(make_settings(log_format=LogFormat.JSON))
    assert structlog.is_configured()


def test_console_format_is_configured() -> None:
    configure_logging(make_settings(log_format=LogFormat.CONSOLE))
    assert structlog.is_configured()


def test_logger_is_bound_to_its_module() -> None:
    configure_logging(make_settings())
    assert get_logger("letmehandle.test") is not None


def test_the_http_client_does_not_log_request_urls_at_the_default_level() -> None:
    # The client logs every request's full URL at info, and a provider's URL carries the account
    # it authenticates as and the identifiers of the calls it is acting on.
    configure_logging(make_settings(log_level="info"))
    try:
        for name in ("httpx", "httpcore"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    finally:
        configure_logging(make_settings())
