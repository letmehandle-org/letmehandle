"""The OpenAI Realtime handshake.

The model travels as the `model` query parameter of the endpoint URL, and the key as an
`Authorization: Bearer` header. A self-run compatible server that needs no key gets no header at
all, rather than an empty one it might reject. Everything after the handshake is the shared
websocket connection's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.speech.session_support.bounds import DEFAULT_OPEN_TIMEOUT_SECONDS
from letmehandle.adapters.speech.websocket.endpoint import with_query_parameter
from letmehandle.adapters.speech.websocket.socket import WebsocketConnection

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )


def websocket_opener(
    endpoint_url: str,
    *,
    model: str,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """Something a session can call, again and again, to get a fresh connection.

    The URL and headers are settled once, here, so that a reconnection cannot quietly differ
    from the connection it replaces.
    """
    uri = with_query_parameter(endpoint_url, "model", model)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def open_connection() -> EventConnection:
        return await WebsocketConnection.open(uri, headers=headers, open_timeout=open_timeout)

    return open_connection
