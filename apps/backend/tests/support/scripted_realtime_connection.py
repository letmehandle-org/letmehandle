"""A realtime speech service in memory, speaking the protocol and nothing else.

It behaves the way the session needs a service to behave, rather than recording calls for a test
to inspect: it acknowledges configuration, answers audio with a spoken response and its
transcript, signals that the caller began speaking, honours a cancel by dropping the output it
had not yet delivered, and can drop a connection or refuse one outright. A session that works
against it has had its reconnection, interruption and cleanup exercised by something with
opinions of its own.

The audio it speaks is synthetic silence generated here, never a recording (D-013).
"""

from __future__ import annotations

import asyncio
import base64
import itertools
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

# Ten milliseconds of 16-bit audio at 24 kHz.
DELTA_BYTES = 480
REPLY_TRANSCRIPT = "happy to help"


@dataclass(frozen=True, slots=True)
class _Outgoing:
    """Something the service will send, and the response it belongs to, if any."""

    payload: Mapping[str, Any] | ConnectionFailedError | None
    response_id: str | None = None


class ScriptedRealtimeService:
    """Hands out connections, and remembers each one it made."""

    def __init__(self, *, answer_audio: bool = True, deltas_per_reply: int = 3) -> None:
        self.answer_audio = answer_audio
        self.deltas_per_reply = deltas_per_reply
        self.connections: list[ScriptedRealtimeConnection] = []
        self.refusals: deque[ConnectionFailedError] = deque()
        # When set, every send waits for it: a service that has stopped answering mid-handshake.
        self.stalled: asyncio.Event | None = None
        # How many more connections to acknowledge and then end at once, and anything said first.
        self.hang_ups_after_configuring = 0
        self.last_words: Sequence[Mapping[str, Any]] = ()
        self._activity = asyncio.Event()
        self._ids = itertools.count(1)

    async def open(self) -> ScriptedRealtimeConnection:
        """The `ConnectionOpener`. Refuses while refusals are queued."""
        if self.refusals:
            raise self.refusals.popleft()
        connection = ScriptedRealtimeConnection(self)
        self.connections.append(connection)
        self.noticed()
        return connection

    def refuse_next(self, *, retryable: bool, times: int = 1) -> None:
        for _ in range(times):
            self.refusals.append(ConnectionFailedError("refused", retryable=retryable))

    @property
    def current(self) -> ScriptedRealtimeConnection:
        return self.connections[-1]

    @property
    def open_connections(self) -> int:
        return sum(1 for connection in self.connections if not connection.closed)

    def next_id(self) -> int:
        return next(self._ids)

    def noticed(self) -> None:
        """Something happened that a waiting test may care about."""
        self._activity.set()

    async def wait_for_sent(self, event_type: str, *, connection: int = 1) -> None:
        """Wait until the `connection`th connection opened has been sent this type of event."""
        await self.wait_until(
            lambda: (
                len(self.connections) >= connection
                and event_type in self.connections[connection - 1].sent_types()
            )
        )

    async def wait_until_delivered(self) -> None:
        """Wait until the current connection has handed over everything it had to say."""
        await self.wait_until(lambda: self.current.pending == 0)

    async def wait_for_connection_count(self, count: int) -> None:
        """Wait until `count` connections have been opened."""
        await self.wait_until(lambda: len(self.connections) >= count)

    async def wait_until(self, satisfied: Callable[[], bool]) -> None:
        """Wait until something about the connections so far is true."""
        # A bound on a wait for something that should already be on its way, so a broken session
        # fails the test rather than hanging the suite.
        async with asyncio.timeout(2):
            while not satisfied():
                self._activity.clear()
                await self._activity.wait()


class ScriptedRealtimeConnection:
    """One connection to the scripted service."""

    def __init__(self, service: ScriptedRealtimeService) -> None:
        self._service = service
        self._outbox: deque[_Outgoing] = deque()
        self._ready = asyncio.Event()
        self._started: set[str] = set()
        self.sent: list[Mapping[str, Any]] = []
        self.closed = False

    # ------------------------------------------------------------------ the connection seam

    async def send(self, event: Mapping[str, Any]) -> None:
        if self._service.stalled is not None:
            await self._service.stalled.wait()
        if self.closed:
            raise ConnectionClosedError("the connection is closed")
        self.sent.append(event)
        self._service.noticed()
        match event.get("type"):
            case "session.update":
                self._push({"type": "session.updated", "session": event["session"]})
                if self._service.hang_ups_after_configuring:
                    self._service.hang_ups_after_configuring -= 1
                    for last_word in self._service.last_words:
                        self._push(last_word)
                    self.hang_up()
            case "input_audio_buffer.append" if self._service.answer_audio:
                self.reply()
            case "response.cancel":
                self._cancel()

    async def receive(self) -> Mapping[str, Any] | None:
        while not self._outbox:
            if self.closed:
                raise ConnectionClosedError("the connection is closed")
            self._ready.clear()
            await self._ready.wait()
        outgoing = self._outbox.popleft()
        self._service.noticed()
        if isinstance(outgoing.payload, ConnectionFailedError):
            self.closed = True
            raise outgoing.payload
        if outgoing.payload is not None and outgoing.response_id is not None:
            self._started.add(outgoing.response_id)
        return outgoing.payload

    async def close(self) -> None:
        self.closed = True
        self._ready.set()

    # ---------------------------------------------------------------- what a test makes happen

    def reply(self, *, deltas: int | None = None, transcript: str = REPLY_TRANSCRIPT) -> str:
        """Queue one whole spoken response, and return its id."""
        number = self._service.next_id()
        response_id, item_id = f"resp_{number}", f"item_{number}"
        self._push({"type": "response.created", "response": {"id": response_id}}, response_id)
        for _ in range(self._service.deltas_per_reply if deltas is None else deltas):
            self._push(audio_delta(response_id, item_id), response_id)
        self._push(
            {"type": "response.output_audio_transcript.delta", "delta": transcript}, response_id
        )
        self._push(
            {"type": "response.output_audio_transcript.done", "transcript": transcript},
            response_id,
        )
        self._push(
            {"type": "response.done", "response": {"id": response_id, "status": "completed"}},
            response_id,
        )
        return response_id

    def caller_starts_speaking(self) -> None:
        self._push({"type": "input_audio_buffer.speech_started", "audio_start_ms": 0})

    def caller_stops_speaking(self) -> None:
        self._push({"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 0})

    def caller_said(self, text: str) -> None:
        self._push({"type": "conversation.item.input_audio_transcription.delta", "delta": text})
        self._push(
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": text}
        )

    def emit(self, event: Mapping[str, Any]) -> None:
        """Send any event at all, known to the protocol or not."""
        self._push(event)

    def drop(self, *, retryable: bool = True) -> None:
        """Fail the connection once everything already queued has been delivered."""
        self._outbox.append(_Outgoing(ConnectionFailedError("dropped", retryable=retryable)))
        self._ready.set()

    def hang_up(self) -> None:
        """End the connection from the service's side."""
        self._outbox.append(_Outgoing(None))
        self._ready.set()

    def sent_types(self) -> list[str]:
        return [str(event["type"]) for event in self.sent]

    def instructions_sent(self) -> list[str]:
        """The instructions of every configuration this connection was sent, in order."""
        return [
            str(event["session"]["instructions"])
            for event in self.sent
            if event["type"] == "session.update"
        ]

    @property
    def pending(self) -> int:
        return len(self._outbox)

    # ------------------------------------------------------------------------------ internals

    def _push(self, payload: Mapping[str, Any], response_id: str | None = None) -> None:
        self._outbox.append(_Outgoing(payload, response_id))
        self._ready.set()

    def _cancel(self) -> None:
        in_flight = {each.response_id for each in self._outbox if each.response_id is not None}
        if not in_flight:
            self._push({"type": "error", "error": {"code": "response_cancel_not_active"}})
            return
        self._outbox = deque(each for each in self._outbox if each.response_id not in in_flight)
        for response_id in sorted(in_flight & self._started):
            done = {"id": response_id, "status": "cancelled"}
            self._push({"type": "response.done", "response": done})


def audio_delta(response_id: str, item_id: str, *, size: int = DELTA_BYTES) -> dict[str, Any]:
    """One piece of synthetic model audio, as the current protocol spells it."""
    return {
        "type": "response.output_audio.delta",
        "response_id": response_id,
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "delta": base64.b64encode(bytes(size)).decode("ascii"),
    }
