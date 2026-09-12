"""Request-scoped concerns that every endpoint needs and none should implement."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware

from letmehandle.observability.logging import correlation_id, get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

logger = get_logger(__name__)

HEADER = "x-correlation-id"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Give every request an id, and put it on every log line it produces.

    An inbound id is honoured so that a caller — a mobile app, or a provider webhook — can tie
    its own logs to ours. It is treated as an opaque label and never as input to anything,
    which is why it is bounded in length before use.
    """

    MAX_LENGTH = 128

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(HEADER, "").strip()
        identifier = incoming[: self.MAX_LENGTH] if incoming else str(uuid.uuid4())
        # Both, deliberately. The context variable reaches every log line without being
        # threaded through call signatures; the request scope outlives this middleware, and is
        # what the error handlers can still read after it has unwound. See errors.py.
        request.state.correlation_id = identifier
        token = correlation_id.set(identifier)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[HEADER] = identifier
        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            correlation_id=identifier,
        )
        return response
