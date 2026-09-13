"""One error shape, and no internals in it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from letmehandle.api import errors
from letmehandle.api.errors import (
    error_body,
    register_error_handlers,
    resolve_correlation_id,
)
from letmehandle.api.middleware import CorrelationMiddleware
from letmehandle.observability.logging import configure_logging, correlation_id
from tests.support.config import make_settings

if TYPE_CHECKING:
    import pytest


def test_error_body_omits_the_correlation_id_when_there_is_none() -> None:
    correlation_id.set(None)
    assert error_body("code", "message") == {"error": "code", "message": "message"}


def test_error_body_includes_the_correlation_id_when_there_is_one() -> None:
    token = correlation_id.set("abc")
    try:
        assert error_body("code", "message")["correlation_id"] == "abc"
    finally:
        correlation_id.reset(token)


def _app_that_fails() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("a secret detail that must not be returned")

    @app.get("/typed/{number}")
    async def typed(number: int) -> dict[str, int]:
        return {"number": number}

    return app


async def test_an_unhandled_error_is_logged_by_where_it_happened_never_by_what_it_said(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A database error's detail repeats the values it refused — a caller's number, say — and a log
    # line outlives the request that caused it. The type and the frames are enough to find it.
    transport = ASGITransport(app=_app_that_fails(), raise_app_exceptions=False)
    # Whatever level an earlier test configured stays in force and would filter the event before
    # the capture sees it, and a logger bound earlier is cached; so the level is set here and the
    # module's logger is rebound inside the capture.
    configure_logging(make_settings(log_level="debug"))
    try:
        with capture_logs() as events:
            monkeypatch.setattr(errors, "logger", structlog.get_logger("letmehandle.api.errors"))
            async with AsyncClient(transport=transport, base_url="http://testserver") as http:
                await http.get("/boom")
    finally:
        configure_logging(make_settings())

    [event] = [each for each in events if each["event"] == "unhandled_exception"]
    assert event["exception"]["type"] == "RuntimeError"
    assert any("boom" in frame for frame in event["exception"]["frames"])
    assert "secret detail" not in repr(event)
    assert "exc_info" not in event


async def test_an_unhandled_error_returns_no_internal_detail() -> None:
    transport = ASGITransport(app=_app_that_fails(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"] == "internal_error"
    assert "secret detail" not in response.text
    assert body["correlation_id"]


async def test_a_malformed_request_is_a_422_with_the_common_shape() -> None:
    transport = ASGITransport(app=_app_that_fails(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/typed/not-a-number")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_the_context_variable_is_used_when_the_request_carries_no_id() -> None:
    """A request that never passed through the middleware still gets an id on its error.

    Not hypothetical: an exception raised before the middleware runs, or a response built
    outside the request cycle, reaches the handlers with an empty state.
    """
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    token = correlation_id.set("from-the-context")
    try:
        assert resolve_correlation_id(request) == "from-the-context"
    finally:
        correlation_id.reset(token)


def test_no_id_is_reported_when_there_is_none_anywhere() -> None:
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    correlation_id.set(None)
    assert resolve_correlation_id(request) is None
