"""What every simulated speech service on a loopback socket shares: serving and idling."""

from __future__ import annotations

import asyncio
import math
import struct
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final, Self

from websockets.asyncio.server import serve

if TYPE_CHECKING:
    from types import TracebackType

    from websockets.asyncio.server import Server, ServerConnection
    from websockets.http11 import Request, Response

# The RMS level below which a chunk is silence, never exact zeros, since resampling leaves residue.
SILENCE_RMS: Final = 500


def is_speech(audio: bytes) -> bool:
    """Whether 16-bit little-endian audio is loud enough to be speech."""
    usable = len(audio) - len(audio) % 2
    if not usable:
        return False
    samples = struct.unpack(f"<{usable // 2}h", audio[:usable])
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples)) >= SILENCE_RMS


class SimulatedService[C](ABC):
    """A speech service on 127.0.0.1 and an ephemeral port, used as an async context manager."""

    path: str

    def __init__(self) -> None:
        self._server: Server | None = None
        self._conversations: dict[ServerConnection, C] = {}
        self._refusing = False
        self._idle = asyncio.Event()
        self._idle.set()

    @abstractmethod
    def _admit(self, connection: ServerConnection, request: Request) -> Response | None:
        """Refuse a handshake with a response, or admit it with `None`."""

    @abstractmethod
    async def _converse(self, connection: ServerConnection) -> None:
        """Hold one admitted connection until it ends."""

    async def __aenter__(self) -> Self:
        self._server = await serve(self._converse, "127.0.0.1", 0, process_request=self._admit)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        server = self._require_server()
        server.close()
        await server.wait_closed()

    @property
    def url(self) -> str:
        port = self._require_server().sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}{self.path}"

    @property
    def open_connections(self) -> int:
        return len(self._conversations)

    async def wait_until_idle(self) -> None:
        """Return once every connection has finished on this side too."""
        await self._idle.wait()

    def refuse_authentication(self) -> None:
        """Answer every later handshake with 401, whatever key it presents."""
        self._refusing = True

    def drop_connections(self) -> None:
        """Cut every connection with no close frame, the way a network failure does."""
        for connection in self._conversations:
            connection.transport.abort()

    def _began(self, connection: ServerConnection, conversation: C) -> None:
        self._conversations[connection] = conversation
        self._idle.clear()

    def _ended(self, connection: ServerConnection) -> None:
        del self._conversations[connection]
        if not self._conversations:
            self._idle.set()

    def _require_server(self) -> Server:
        if self._server is None:
            raise RuntimeError("the simulated service is used as an async context manager")
        return self._server
