"""The boundary between this adapter and the network.

Everything above this module speaks the protocol as plain JSON-shaped mappings: what to send,
and what came back. Everything below it is the websocket, its handshake and its credentials.
Drawing the line here lets the session — reconnection, interruption, backpressure, cleanup — be
tested against a connection that speaks the same protocol with no network at all, while the real
connection is tested separately against an in-process server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


class RealtimeConnection(Protocol):
    """One open connection to a realtime speech service."""

    async def send(self, event: Mapping[str, Any]) -> None:
        """Send one protocol event.

        Raises `ConnectionClosedError` once the connection has closed, and
        `ConnectionFailedError` when it fails — never a library exception.
        """

    async def receive(self) -> Mapping[str, Any] | None:
        """The next protocol event, or `None` once the service has closed the connection.

        Raises `ConnectionFailedError` when the connection fails mid-stream.
        """

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""


# How a session gets a connection: something it can call again, which is what reconnection is.
type ConnectionOpener = Callable[[], Awaitable[RealtimeConnection]]


class RealtimeConnectionError(Exception):
    """Base for the ways a connection stops working.

    Adapter-internal. The session translates these into the domain's own errors and events, so
    nothing above the adapter ever catches one.
    """


class ConnectionClosedError(RealtimeConnectionError):
    """The connection was used after it closed."""


class ConnectionFailedError(RealtimeConnectionError):
    """The connection failed.

    `retryable` is the fact reconnection needs: a dropped connection or a timeout is worth
    another attempt, and a refused key or an unknown model is not.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
