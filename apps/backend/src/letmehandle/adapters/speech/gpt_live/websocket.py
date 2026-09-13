"""The GPT-Live handshake.

The endpoint is used as it is, with no query parameters: the model travels in the session's opening
message, not the URL. The key is sent as an `Authorization: Bearer` header, and a server that needs
no key gets no header at all. Everything after the handshake is the shared websocket connection's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.speech.session_support.bounds import DEFAULT_OPEN_TIMEOUT_SECONDS
from letmehandle.adapters.speech.websocket.socket import WebsocketConnection

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )


def websocket_opener(
    endpoint_url: str,
    *,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """Something a session can call, again and again, to get a fresh connection.

    The URL and headers are settled once, here, so that a reconnection cannot quietly differ from
    the connection it replaces.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def open_connection() -> EventConnection:
        return await WebsocketConnection.open(
            endpoint_url, headers=headers, open_timeout=open_timeout
        )

    return open_connection
