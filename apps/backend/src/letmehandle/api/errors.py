"""One mapping from exception to response, so error shape cannot vary by endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from letmehandle.observability.logging import correlation_id, get_logger
from letmehandle.observability.scrubbing import exception_outline

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class ApiError(Exception):
    """A failure with a status code and a stable machine-readable code.

    The code is what a client branches on. A message is for a person and will be rewritten;
    anything that parses one has turned prose into an interface.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


# Starlette renamed this constant; the old name still resolves but warns.
UNPROCESSABLE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


def resolve_correlation_id(request: Request | None) -> str | None:
    """Find the correlation id for this request, wherever it is still reachable.

    The request's state is tried first and the context variable second, and the order is not
    arbitrary. An unhandled exception is turned into a response by Starlette's outermost
    error middleware, which runs *after* our own middleware has unwound and reset the context
    variable — so on the one response where the id matters most, the context variable is
    already gone. The request scope is not: it outlives the middleware that populated it.
    """
    if request is not None:
        from_state = getattr(request.state, "correlation_id", None)
        if isinstance(from_state, str):
            return from_state
    return correlation_id.get()


def error_body(code: str, message: str, request: Request | None = None) -> dict[str, str]:
    """The one error shape this application returns.

    The correlation id is included so that someone reporting a failure gives us the single
    string that finds every log line for it, without needing anything else from them.
    """
    body = {"error": code, "message": message}
    identifier = resolve_correlation_id(request)
    if identifier is not None:
        body["correlation_id"] = identifier
    return body


def _readable_detail(exception: Exception) -> list[dict[str, str]]:
    """What was wrong with the request, as plain strings.

    Rebuilt rather than passed through. pydantic puts the original exception object into each
    error's context, which is not serialisable — handing the raw list to a JSON response turns
    every malformed request into a 500, which is how a validation bug becomes an outage. It is
    also more than a caller needs: the field and the reason, nothing from inside the process.
    """
    if not isinstance(exception, RequestValidationError):  # pragma: no cover - by registration
        return []
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ()) if part != "body"),
            "problem": str(error.get("msg", "is not valid")),
        }
        for error in exception.errors()
    ]


async def handle_validation_error(request: Request, exception: Exception) -> JSONResponse:
    """A malformed request. The detail is safe to return: it describes what was sent."""
    return JSONResponse(
        status_code=UNPROCESSABLE,
        content={
            **error_body("invalid_request", "The request could not be understood.", request),
            "detail": _readable_detail(exception),
        },
    )


async def handle_api_error(request: Request, exception: Exception) -> JSONResponse:
    """A failure the application raised deliberately."""
    if not isinstance(exception, ApiError):  # pragma: no cover - registered by type
        raise exception
    return JSONResponse(
        status_code=exception.status_code,
        content=error_body(exception.code, exception.message, request),
        headers=exception.headers,
    )


async def handle_unexpected_error(request: Request, exception: Exception) -> JSONResponse:
    """Anything not otherwise mapped.

    The response carries a correlation id and nothing else. A stack trace tells an attacker
    about the inside of the process and tells the caller nothing they can act on.
    """
    # Where it happened and what kind of failure it was, never its message. A database error's
    # detail repeats the values it refused, and a domain error's message can repeat a caller's
    # number; a log line is kept longer and shared more widely than the request that caused it.
    logger.error("unhandled_exception", exception=exception_outline(exception))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_body(
            "internal_error",
            "Something went wrong. Quote the correlation id if you report this.",
            request,
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Install the handlers. The only place they are installed."""
    handlers: dict[
        type[Exception] | int, Callable[[Request, Exception], Awaitable[JSONResponse]]
    ] = {
        ApiError: handle_api_error,
        RequestValidationError: handle_validation_error,
        Exception: handle_unexpected_error,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, handler)
