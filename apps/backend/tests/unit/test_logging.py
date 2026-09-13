"""Every line must be able to say which request produced it."""

from __future__ import annotations

import asyncio
import logging

import pytest
import structlog
from structlog.testing import capture_logs

from letmehandle.config.settings import LogFormat
from letmehandle.domain.errors import ProviderError
from letmehandle.observability.logging import (
    add_correlation_id,
    bind_call,
    configure_logging,
    correlation_id,
    get_logger,
    log_failure,
)
from letmehandle.observability.scrubbing import exception_outline
from letmehandle.observability.tracing import UNTRACEABLE_CALL
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


def test_the_console_renders_an_outlined_exception_without_failing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Failures are logged as an outline, a mapping, and the console renderer expects text there.
    configure_logging(make_settings(log_format=LogFormat.CONSOLE, log_level="info"))
    try:
        outline = exception_outline(RuntimeError("boom"))
        get_logger("letmehandle.test").error("unhandled_exception", exception=outline)
        written = capsys.readouterr().err
    finally:
        configure_logging(make_settings())
    assert "unhandled_exception" in written
    assert "RuntimeError" in written


def test_logger_is_bound_to_its_module() -> None:
    configure_logging(make_settings())
    assert get_logger("letmehandle.test") is not None


def test_the_http_client_does_not_log_request_urls_at_the_default_level() -> None:
    # The HTTP client's request URLs carry account and call identifiers.
    configure_logging(make_settings(log_level="info"))
    try:
        for name in ("httpx", "httpcore"):
            assert not logging.getLogger(name).isEnabledFor(logging.INFO), name
    finally:
        configure_logging(make_settings())


async def test_a_calls_run_puts_its_id_and_its_request_on_its_own_lines_and_nobody_elses() -> None:
    async def run(call: str, request: str | None) -> tuple[object, object]:
        bind_call(call, request)
        await asyncio.sleep(0)
        return structlog.contextvars.get_contextvars()["call_id"], correlation_id.get()

    first, second = await asyncio.gather(
        asyncio.create_task(run("CAsim-1", "request-1")), asyncio.create_task(run("CAsim-2", None))
    )

    assert first == ("CAsim-1", "request-1")
    assert second == ("CAsim-2", None)
    assert "call_id" not in structlog.contextvars.get_contextvars()


async def test_a_call_id_that_could_be_a_number_is_not_bound_as_one() -> None:
    async def run() -> object:
        bind_call("12025550123", None)
        return structlog.contextvars.get_contextvars()["call_id"]

    assert await asyncio.create_task(run()) == UNTRACEABLE_CALL


@pytest.mark.parametrize(
    ("error", "level", "kind"),
    [
        (TimeoutError(), "warning", "timeout"),
        (
            ProviderError("speech", "+12025550123 not reachable", retryable=True),
            "warning",
            "unavailable",
        ),
        (RuntimeError("a mistake"), "error", "defect"),
    ],
)
def test_a_handled_failure_is_logged_by_kind_at_the_level_it_deserves(
    error: BaseException, level: str, kind: str
) -> None:
    # Whatever an earlier test configured could filter the line before the capture sees it.
    configured = structlog.get_config()
    structlog.reset_defaults()
    try:
        with capture_logs() as events:
            log_failure(
                structlog.get_logger("letmehandle.test"), "call.failed", error, stage="dial"
            )
    finally:
        structlog.configure(**configured)

    assert events == [
        {
            "event": "call.failed",
            "log_level": level,
            "error": type(error).__name__,
            "kind": kind,
            "stage": "dial",
        }
    ]
