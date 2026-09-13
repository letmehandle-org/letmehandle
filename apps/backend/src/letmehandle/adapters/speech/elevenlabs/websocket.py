"""The ElevenLabs Agents handshake: the agent in `agent_id`, the key as an `xi-api-key` header."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.speech.session_support.bounds import DEFAULT_OPEN_TIMEOUT_SECONDS
from letmehandle.adapters.speech.websocket.endpoint import with_query_parameter
from letmehandle.adapters.speech.websocket.socket import opener_for

if TYPE_CHECKING:
    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener


def websocket_opener(
    endpoint_url: str,
    *,
    agent_id: str,
    api_key: str | None = None,
    open_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
) -> ConnectionOpener:
    """An opener for conversations with `agent_id`; a public agent is sent no key."""
    return opener_for(
        with_query_parameter(endpoint_url, "agent_id", agent_id),
        headers={"xi-api-key": api_key} if api_key else {},
        open_timeout=open_timeout,
    )
