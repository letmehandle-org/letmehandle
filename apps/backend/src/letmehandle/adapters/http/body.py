"""Reading a request body no larger than the route can have a use for.

A body is read before anything in it can be checked — a signature is computed over it, a schema
is applied to it — so a route that simply reads what it is sent holds whatever an unauthenticated
client chooses to send. The declared length is refused first, because it costs nothing to check;
the body is then counted as it arrives, because a length is only declared when the client
chooses to declare one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request


class BodyTooLargeError(Exception):
    """The request's body is larger than the route reads."""


async def read_limited_body(request: Request, limit: int) -> bytes:
    """The request's body, or `BodyTooLargeError` once it is known to exceed `limit` bytes."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise BodyTooLargeError
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise BodyTooLargeError
    return bytes(body)
