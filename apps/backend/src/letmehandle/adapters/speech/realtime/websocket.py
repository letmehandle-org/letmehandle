"""The realtime connection, over a real websocket.

The handshake is the one the OpenAI Realtime protocol documents: the model travels as the
`model` query parameter of the endpoint URL, and the key as an `Authorization: Bearer` header.
A self-run compatible server that needs no key gets no header at all, rather than an empty one
it might reject.

Every exception the library can raise is translated here, and each translation decides one
thing: whether trying again could help. That decision is the whole value of this module to the
session above it. A refused key retried with backoff is a loop that never ends and looks, from
outside, exactly like an outage.

Nothing here logs. The failures it raises carry status and close codes, never the key and never
a message payload, and the session — which knows whether a failure matters — is the one that
reports them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import (
    ConnectionClosedError as WebsocketClosedError,
)
from websockets.exceptions import (
    ConnectionClosedOK,
    InvalidHandshake,
    InvalidMessage,
    InvalidStatus,
    InvalidURI,
)

from letmehandle.adapters.speech.realtime.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from websockets.asyncio.client import ClientConnection

    from letmehandle.adapters.speech.realtime.connection import (
        ConnectionOpener,
        RealtimeConnection,
    )

# How long a handshake may take before it counts as a failure worth retrying. Long enough for a
# service that is starting a model, short enough that a caller is not left listening to nothing.
DEFAULT_OPEN_TIMEOUT_SECONDS: Final = 10.0

# Refusals at the handshake that another attempt will not change: bad request, bad key, not
# allowed, no such model. Everything else a server answers with — a timeout, a rate limit, a
# fault of its own — is its condition rather than ours, and may have passed by the next attempt.
_RETRYABLE_STATUSES: Final = frozenset({408, 425, 429})

# Close codes that say the service will refuse the same connection again: a protocol or data it
# does not accept, a policy it enforces (which is where a key refused after the handshake lands),
# or a message too large to take. An abrupt drop, a server error or a restart is none of these.
_PERMANENT_CLOSE_CODES: Final = frozenset({1002, 1003, 1007, 1008, 1009, 1010})


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

    async def open_connection() -> RealtimeConnection:
        return await WebsocketConnection.open(uri, headers=headers, open_timeout=open_timeout)

    return open_connection


class WebsocketConnection:
    """One open websocket to a realtime speech service, speaking protocol events."""

    def __init__(self, socket: ClientConnection) -> None:
        self._socket = socket
        self._closed = False

    @classmethod
    async def open(
        cls, uri: str, *, headers: Mapping[str, str], open_timeout: float
    ) -> WebsocketConnection:
        """Complete the handshake, or raise a failure that says whether to try again."""
        try:
            socket = await connect(
                uri,
                additional_headers=dict(headers),
                open_timeout=open_timeout,
            )
        except InvalidStatus as error:
            status = error.response.status_code
            raise ConnectionFailedError(
                f"the service refused the connection with HTTP {status}",
                retryable=status in _RETRYABLE_STATUSES or status >= 500,
            ) from None
        except InvalidURI:
            # Configuration, not weather. Suppressed rather than chained: the library's message
            # repeats the URL, and the URL is not this module's to put in a traceback.
            raise ConnectionFailedError(
                "the speech endpoint is not a websocket URL", retryable=False
            ) from None
        except InvalidMessage:
            # The connection went away mid-handshake, which is what a restarting service does.
            raise ConnectionFailedError(
                "the connection was lost during the handshake", retryable=True
            ) from None
        except InvalidHandshake as error:
            raise ConnectionFailedError(
                f"the service does not speak the websocket handshake: {type(error).__name__}",
                retryable=False,
            ) from None
        except TimeoutError:
            raise ConnectionFailedError(
                "the service did not complete the handshake in time", retryable=True
            ) from None
        except OSError as error:
            # Refused, unreachable, reset: the service is not there right now.
            raise ConnectionFailedError(
                f"the service could not be reached: {type(error).__name__}", retryable=True
            ) from None
        return cls(socket)

    async def send(self, event: Mapping[str, Any]) -> None:
        if self._closed:
            raise ConnectionClosedError("the connection was used after it was closed")
        text = json.dumps(event, separators=(",", ":"))
        try:
            await self._socket.send(text)
        except ConnectionClosedOK:
            raise ConnectionClosedError("the service had already closed the connection") from None
        except WebsocketClosedError as error:
            raise _failure_from_close(error) from None

    async def receive(self) -> Mapping[str, Any] | None:
        if self._closed:
            raise ConnectionClosedError("the connection was used after it was closed")
        try:
            frame = await self._socket.recv()
        except ConnectionClosedOK:
            return None
        except WebsocketClosedError as error:
            raise _failure_from_close(error) from None
        return _parse(frame)

    async def close(self) -> None:
        # The library's close is idempotent too; the flag is what makes a send after it a typed
        # error of ours rather than whatever the library happens to say.
        self._closed = True
        await self._socket.close()


def _parse(frame: str | bytes) -> Mapping[str, Any]:
    """One protocol event from one frame.

    A frame that is not a JSON object means the other end is not speaking this protocol, and a
    reconnect lands on the same server speaking the same thing — so it is not retryable. The
    frame's contents never reach the message: it may be a transcript.
    """
    if isinstance(frame, bytes):
        raise ConnectionFailedError("the service sent a binary frame", retryable=False)
    try:
        event = json.loads(frame)
    except json.JSONDecodeError:
        raise ConnectionFailedError(
            "the service sent a frame that is not JSON", retryable=False
        ) from None
    if not isinstance(event, dict):
        raise ConnectionFailedError(
            "the service sent a frame that is not a JSON object", retryable=False
        )
    return event


def _failure_from_close(error: WebsocketClosedError) -> ConnectionFailedError:
    # `rcvd` is None when no close frame arrived at all, which is a dropped connection.
    code = error.rcvd.code if error.rcvd is not None else 1006
    return ConnectionFailedError(
        f"the connection closed abnormally with code {code}",
        retryable=code not in _PERMANENT_CLOSE_CODES,
    )


def _with_model(endpoint_url: str, model: str) -> str:
    """The endpoint with the model as its `model` query parameter, replacing any already there."""
    parts = urlsplit(endpoint_url)
    query = [(name, value) for name, value in parse_qsl(parts.query) if name != "model"]
    query.append(("model", model))
    return urlunsplit(parts._replace(query=urlencode(query)))
