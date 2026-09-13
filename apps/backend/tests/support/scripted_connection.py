"""What every scripted speech service in memory shares: handing out connections, waiting on them."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


@dataclass(frozen=True, slots=True)
class Queued:
    """Something a connection will deliver, and the response it belongs to, if any."""

    payload: Mapping[str, Any] | ConnectionFailedError | None
    response_id: str | None = None


class ScriptedService[C: ScriptedConnection[Any]](ABC):
    """Hands out connections, and remembers each one it made."""

    def __init__(self) -> None:
        self.connections: list[C] = []
        self.refusals: deque[ConnectionFailedError] = deque()
        # When set, every send waits for it: a service that has stopped answering.
        self.stalled: asyncio.Event | None = None
        self._activity = asyncio.Event()

    @abstractmethod
    def _connection(self) -> C:
        """A new connection to this service."""

    async def open(self) -> C:
        """The `ConnectionOpener`, refusing while refusals are queued."""
        if self.refusals:
            raise self.refusals.popleft()
        connection = self._connection()
        self.connections.append(connection)
        self.noticed()
        return connection

    def refuse_next(self, *, retryable: bool, times: int = 1) -> None:
        for _ in range(times):
            self.refusals.append(ConnectionFailedError("refused", retryable=retryable))

    @property
    def current(self) -> C:
        return self.connections[-1]

    @property
    def open_connections(self) -> int:
        return sum(1 for connection in self.connections if not connection.closed)

    def noticed(self) -> None:
        """Something happened that a waiting test may care about."""
        self._activity.set()

    async def wait_for_sent(self, event_type: str, *, connection: int = 1, count: int = 1) -> None:
        """Wait until the `connection`th connection has been sent `count` events of a type."""
        await self.wait_until(
            lambda: (
                len(self.connections) >= connection
                and self.connections[connection - 1].sent_types().count(event_type) >= count
            )
        )

    async def wait_for_connection_count(self, count: int) -> None:
        await self.wait_until(lambda: len(self.connections) >= count)

    async def wait_until_delivered(self) -> None:
        """Wait until the current connection has handed over everything it had to say."""
        await self.wait_until(lambda: self.current.pending == 0)

    async def wait_until(self, satisfied: Callable[[], bool]) -> None:
        """Wait, for two seconds at most, until something about the connections is true."""
        async with asyncio.timeout(2):
            while not satisfied():
                self._activity.clear()
                await self._activity.wait()


class ScriptedConnection[S: ScriptedService[Any]](ABC):
    """One connection to a scripted service, answering what it is sent as that service would."""

    def __init__(self, service: S) -> None:
        self._service = service
        self._outbox: deque[Queued] = deque()
        self._ready = asyncio.Event()
        self.sent: list[Mapping[str, Any]] = []
        self.closed = False

    @abstractmethod
    def _answer(self, event: Mapping[str, Any]) -> None:
        """Queue whatever the service says in answer to `event`."""

    def _taken(self, queued: Queued) -> None:
        """Note that `queued` reached the client."""
        del queued

    async def send(self, event: Mapping[str, Any]) -> None:
        if self._service.stalled is not None:
            await self._service.stalled.wait()
        if self.closed:
            raise ConnectionClosedError("the connection is closed")
        self.sent.append(event)
        self._service.noticed()
        self._answer(event)

    async def receive(self) -> Mapping[str, Any] | None:
        while not self._outbox:
            if self.closed:
                raise ConnectionClosedError("the connection is closed")
            self._ready.clear()
            await self._ready.wait()
        queued = self._outbox.popleft()
        self._service.noticed()
        if isinstance(queued.payload, ConnectionFailedError):
            self.closed = True
            raise queued.payload
        self._taken(queued)
        return queued.payload

    async def close(self) -> None:
        self.closed = True
        self._ready.set()

    def emit(self, event: Mapping[str, Any]) -> None:
        """Send any event at all, known to the protocol or not."""
        self._push(event)

    def drop(self, *, retryable: bool = True) -> None:
        """Fail the connection once everything already queued has been delivered."""
        self._outbox.append(Queued(ConnectionFailedError("dropped", retryable=retryable)))
        self._ready.set()

    def hang_up(self) -> None:
        """End the connection from the service's side."""
        self._outbox.append(Queued(None))
        self._ready.set()

    def sent_types(self) -> list[str]:
        return [str(event.get("type")) for event in self.sent]

    def sent_of(self, event_type: str) -> list[Mapping[str, Any]]:
        return [event for event in self.sent if event.get("type") == event_type]

    @property
    def pending(self) -> int:
        return len(self._outbox)

    def _push(self, payload: Mapping[str, Any], response_id: str | None = None) -> None:
        self._outbox.append(Queued(payload, response_id))
        self._ready.set()
