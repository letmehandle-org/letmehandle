"""The ElevenLabs Agents handshake.

The agent travels as the `agent_id` query parameter of the endpoint URL, and the key as an
`xi-api-key` header. A public agent needs no key and gets no header at all.

The header rather than a signed URL, deliberately. A signed URL exists so that a browser or a
phone can start a conversation without ever holding the key: a server fetches one with the key
and hands it over. This adapter runs on that server already, so a signed URL would cost an extra
request before every connection — including every reconnect, since one is only good for fifteen
minutes — to keep the key from a process that holds it anyway. Everything after the handshake is
the shared websocket connection's, which never puts a header in a message or a log.
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
    agent_id: str,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """Something a session can call, again and again, to get a fresh connection.

    The URL and headers are settled once, here, so that a reconnection cannot quietly differ
    from the connection it replaces.
    """
    uri = with_query_parameter(endpoint_url, "agent_id", agent_id)
    headers = {"xi-api-key": api_key} if api_key else {}

    async def open_connection() -> EventConnection:
        return await WebsocketConnection.open(uri, headers=headers, open_timeout=open_timeout)

    return open_connection
