"""The boundary between a speech session and the network: JSON-shaped events in and out."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


class EventConnection(Protocol):
    """One open connection to a speech service that speaks in JSON events."""

    async def send(self, event: Mapping[str, Any]) -> None:
        """Send one event; raises only `ConnectionClosedError` or `ConnectionFailedError`."""

    async def receive(self) -> Mapping[str, Any] | None:
        """The next event, `None` once the service closed; raises `ConnectionFailedError`."""

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""


# How a session gets a connection, called again for every reconnection.
type ConnectionOpener = Callable[[], Awaitable[EventConnection]]


class EventConnectionError(Exception):
    """Base for the ways a connection stops working, never raised past the adapter."""


class ConnectionClosedError(EventConnectionError):
    """The connection was used after it closed."""


class ConnectionFailedError(EventConnectionError):
    """The connection failed; `retryable` says whether another attempt could succeed."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable
