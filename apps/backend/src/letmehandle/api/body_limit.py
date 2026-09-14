"""Routes whose request body is capped as the handler is entered, before the framework parses it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from fastapi import Request, status
from fastapi.routing import APIRoute

from letmehandle.adapters.http.body import BodyTooLargeError, read_limited_body
from letmehandle.api.errors import ApiError

# The largest body an ordinary JSON route reads.
JSON_BODY_LIMIT_BYTES: Final = 256 * 1024

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from starlette.responses import Response
    from starlette.types import Message


def limited_body_route(limit: int) -> type[APIRoute]:
    """A route class that refuses a body larger than `limit` bytes with a 413."""

    class LimitedBodyRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handle = super().get_route_handler()

            async def limited(request: Request) -> Response:
                try:
                    body = await read_limited_body(request, limit)
                except BodyTooLargeError:
                    raise ApiError(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        "payload_too_large",
                        "This request is larger than this endpoint accepts.",
                    ) from None
                return await handle(Request(request.scope, _replaying(body, request)))

            return limited

    return LimitedBodyRoute


def _replaying(body: bytes, request: Request) -> Callable[[], Awaitable[Message]]:
    """A receive that gives the body already read, then whatever the connection says next."""
    unread: list[Message] = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> Message:
        return unread.pop() if unread else await request.receive()

    return receive
