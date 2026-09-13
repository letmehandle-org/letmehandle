"""A connection that speaks protocol events, over a real websocket.

Shared by every speech adapter whose service is a websocket carrying JSON events. What differs
between services — where the key goes, how a model or an agent is named — is each adapter's
handshake; what happens once connected is the same, and lives here once.

Every exception the library can raise is translated here, and each translation decides one
thing: whether trying again could help. That decision is the whole value of this module to the
session above it. A refused key retried with backoff is a loop that never ends and looks, from
outside, exactly like an outage.

Nothing here logs. The failures it raises carry status and close codes, never the key and never
a message payload, and the session — which knows whether a failure matters — is the one that
reports them.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Final

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

from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from websockets.asyncio.client import ClientConnection

    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener, EventConnection


# How long closing waits for the service before the socket is simply dropped. A service that has
# stopped reading otherwise turns closing into half a minute during which a cancelled call, or a
# harness told to quit, hangs.
CLOSE_TIMEOUT_SECONDS: Final = 1.0

# Refusals at the handshake that another attempt will not change: bad request, bad key, not
# allowed, no such model. Everything else a server answers with — a timeout, a rate limit, a
# fault of its own — is its condition rather than ours, and may have passed by the next attempt.
_RETRYABLE_STATUSES: Final = frozenset({408, 425, 429})

# Close codes that say the service will refuse the same connection again: a protocol or data it
# does not accept, a policy it enforces (which is where a key refused after the handshake lands),
# or a message too large to take. An abrupt drop, a server error or a restart is none of these.
_PERMANENT_CLOSE_CODES: Final = frozenset({1002, 1003, 1007, 1008, 1009, 1010})


def opener_for(uri: str, *, headers: Mapping[str, str], open_timeout: float) -> ConnectionOpener:
    """An opener that connects to the same URI with the same headers every time it is called."""
    fixed_headers = dict(headers)

    async def open_connection() -> EventConnection:
        return await WebsocketConnection.open(uri, headers=fixed_headers, open_timeout=open_timeout)

    return open_connection


def bearer_headers(api_key: str | None) -> dict[str, str]:
    """An `Authorization: Bearer` header, or none at all for a service that needs no key."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


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
                close_timeout=CLOSE_TIMEOUT_SECONDS,
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
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
                await self._socket.close()
        except TimeoutError:
            # The library's own close waits behind a send that cannot finish before its timeout
            # even starts, so it is bounded here as well. A service that will not take a close
            # frame gets its socket dropped instead.
            self._socket.transport.abort()


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
