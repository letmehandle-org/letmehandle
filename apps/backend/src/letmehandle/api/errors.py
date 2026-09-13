"""One mapping from exception to response, so error shape cannot vary by endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from letmehandle.domain.errors import ProviderError
from letmehandle.observability.logging import correlation_id, get_logger
from letmehandle.observability.scrubbing import exception_outline

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class ApiError(Exception):
    """A failure with a status and a stable code clients branch on; the message is for people."""

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


UNPROCESSABLE: Final = status.HTTP_422_UNPROCESSABLE_CONTENT

PROVIDER_UNAVAILABLE_MESSAGE: Final = "A service this depends on is unavailable. Try again shortly."


def invalid_request(error: Exception) -> ApiError:
    """A request whose values the domain refuses."""
    return ApiError(UNPROCESSABLE, "invalid_request", str(error))


def rate_limited(retry_after_seconds: int, message: str) -> ApiError:
    """Too many of something, and when to try again."""
    return ApiError(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "rate_limited",
        message,
        headers={"Retry-After": str(retry_after_seconds)},
    )


def provider_unavailable(retry_after_seconds: int) -> ApiError:
    """A provider could not answer, and when to try again."""
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "provider_unavailable",
        PROVIDER_UNAVAILABLE_MESSAGE,
        headers={"Retry-After": str(retry_after_seconds)},
    )


def database_unavailable() -> ApiError:
    """No database is connected to this process."""
    return ApiError(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "database_unavailable",
        "This service is not connected to its database.",
    )


def resolve_correlation_id(request: Request | None) -> str | None:
    """The request's correlation id, from its state first, since that outlives the middleware."""
    if request is not None:
        from_state = getattr(request.state, "correlation_id", None)
        if isinstance(from_state, str):
            return from_state
    return correlation_id.get()


def error_body(code: str, message: str, request: Request | None = None) -> dict[str, str]:
    """The one error shape this application returns, with the correlation id when there is one."""
    body = {"error": code, "message": message}
    identifier = resolve_correlation_id(request)
    if identifier is not None:
        body["correlation_id"] = identifier
    return body


def _readable_detail(exception: Exception) -> list[dict[str, str]]:
    """Each invalid field and its problem, as plain serialisable strings."""
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


async def handle_provider_error(request: Request, exception: Exception) -> JSONResponse:
    """An unhandled provider failure, answered 503 and logged by provider and reason only."""
    if not isinstance(exception, ProviderError):  # pragma: no cover - registered by type
        raise exception
    logger.warning(
        "provider_failed",
        provider=exception.provider,
        reason=exception.reason,
        retryable=exception.retryable,
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=error_body("provider_unavailable", PROVIDER_UNAVAILABLE_MESSAGE, request),
    )


async def handle_unexpected_error(request: Request, exception: Exception) -> JSONResponse:
    """Anything not otherwise mapped: a 500 carrying only the correlation id."""
    # The outline only, never the message, which can repeat a refused value or a number.
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
        ProviderError: handle_provider_error,
        Exception: handle_unexpected_error,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, handler)
