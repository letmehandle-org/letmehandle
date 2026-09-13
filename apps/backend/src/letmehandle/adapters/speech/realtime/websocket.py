"""The OpenAI Realtime handshake: the model in the `model` parameter, the key as a bearer token."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.speech.session_support.bounds import DEFAULT_OPEN_TIMEOUT_SECONDS
from letmehandle.adapters.speech.websocket.endpoint import with_query_parameter
from letmehandle.adapters.speech.websocket.socket import bearer_headers, opener_for

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener


def websocket_opener(
    endpoint_url: str,
    *,
    model: str,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """An opener for sessions with `model` at `endpoint_url`."""
    return opener_for(
        with_query_parameter(endpoint_url, "model", model),
        headers=bearer_headers(api_key),
        open_timeout=open_timeout,
    )
