"""The GPT-Live handshake: the endpoint as given, the key as a bearer token."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.speech.session_support.bounds import DEFAULT_OPEN_TIMEOUT_SECONDS
from letmehandle.adapters.speech.websocket.socket import bearer_headers, opener_for

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener


def websocket_opener(
    endpoint_url: str,
    *,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """An opener for sessions at `endpoint_url`; the model travels in each session's first event."""
    return opener_for(endpoint_url, headers=bearer_headers(api_key), open_timeout=open_timeout)
