"""Reading a request body, refused by declared length and counted as it arrives."""

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
