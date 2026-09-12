"""The OpenAI Realtime handshake.

The model travels as the `model` query parameter of the endpoint URL, and the key as an
`Authorization: Bearer` header. A self-run compatible server that needs no key gets no header at
all, rather than an empty one it might reject. Everything after the handshake is the shared
websocket connection's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from letmehandle.adapters.speech.websocket.socket import WebsocketConnection

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )

# How long a handshake may take before it counts as a failure worth retrying. Long enough for a
# service that is starting a model, short enough that a caller is not left listening to nothing.
DEFAULT_OPEN_TIMEOUT_SECONDS: Final = 10.0


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
    uri = _with_model(endpoint_url, model)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def open_connection() -> EventConnection:
        return await WebsocketConnection.open(uri, headers=headers, open_timeout=open_timeout)

    return open_connection


def _with_model(endpoint_url: str, model: str) -> str:
    """The endpoint with the model as its `model` query parameter, replacing any already there."""
    parts = urlsplit(endpoint_url)
    query = [(name, value) for name, value in parse_qsl(parts.query) if name != "model"]
    query.append(("model", model))
    return urlunsplit(parts._replace(query=urlencode(query)))
