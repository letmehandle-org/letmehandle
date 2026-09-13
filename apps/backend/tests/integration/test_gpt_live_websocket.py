"""The GPT-Live handshake, against a websocket server on loopback.

No database and no account. What this proves is what the session above cannot see: that the
endpoint is used exactly as given, with no query parameters, and that the key travels as a bearer
header only when there is one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from websockets.asyncio.server import ServerConnection, serve

from letmehandle.adapters.speech.gpt_live.websocket import websocket_opener

if TYPE_CHECKING:
    from websockets.http11 import Request


async def handshake(api_key: str | None) -> tuple[str, Any]:
    seen: dict[str, Any] = {}

    def record(_: ServerConnection, request: Request) -> None:
        seen["path"] = request.path
        seen["authorization"] = request.headers.get("Authorization")

    async def answer(connection: ServerConnection) -> None:
        async for message in connection:
            await connection.send(message)

    async with serve(answer, "127.0.0.1", 0, process_request=record) as server:
        port = server.sockets[0].getsockname()[1]
        opener = websocket_opener(f"ws://127.0.0.1:{port}/v1/live/sessions", api_key=api_key)
        connection = await opener()
        try:
            await connection.send({"type": "session.close"})
            assert await connection.receive() == {"type": "session.close"}
        finally:
            await connection.close()
    return seen["path"], seen["authorization"]


async def test_the_key_travels_as_a_bearer_header_to_the_endpoint_as_given() -> None:
    assert await handshake("an-example-key") == ("/v1/live/sessions", "Bearer an-example-key")


async def test_a_server_that_needs_no_key_is_sent_no_header() -> None:
    assert await handshake(None) == ("/v1/live/sessions", None)
