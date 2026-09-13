"""A media socket in memory: what the provider sends is scripted, what it is sent is kept."""

from __future__ import annotations

import asyncio
import base64
import json

from letmehandle.adapters.transport.twilio.stream import MediaSocketClosedError


class MemoryMediaSocket:
    """Behaves as an accepted websocket does, including after either end has closed it."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []
        self.closed = False
        self.closes = 0

    def provider_sends(self, message: object) -> None:
        self._incoming.put_nowait(message if isinstance(message, str) else json.dumps(message))

    def provider_closes(self) -> None:
        self._incoming.put_nowait(None)

    async def receive(self) -> str | None:
        if self.closed:
            return None
        return await self._incoming.get()

    async def send(self, text: str) -> None:
        if self.closed:
            raise MediaSocketClosedError
        self.sent.append(json.loads(text))

    async def close(self) -> None:
        self.closes += 1
        self.closed = True
        self._incoming.put_nowait(None)

    def audio_sent(self) -> bytes:
        return b"".join(
            base64.b64decode(message["media"]["payload"])  # type: ignore[index]
            for message in self.sent
            if message["event"] == "media"
        )

    def events_sent(self) -> list[object]:
        return [message["event"] for message in self.sent]
