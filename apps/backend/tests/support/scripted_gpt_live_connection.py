"""A GPT-Live service in memory, speaking the protocol and nothing else.

It behaves the way the session needs a service to behave, rather than recording calls for a test to
inspect: it starts a session, acknowledges every append, answers audio with speech and its words,
finalises a session it is asked to close with the voice time it used, and can refuse a session,
drop a connection or close a session of its own accord. Its timeline advances with the audio it
speaks, as the real service's does.

The audio it speaks is synthetic — a square wave for speech, zeros for silence — never a recording
(D-013).
"""

from __future__ import annotations

import asyncio
import base64
from collections import deque
from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# Each piece of audio the service speaks lasts this long, whatever the session's format.
DELTA_MS = 20
REPLY_TRANSCRIPT = "happy to help"
USAGE_SECONDS = 12.0

# A loud square wave and silence, twenty milliseconds of each, in every format the protocol carries.
_AUDIO = {
    "audio/pcmu": (bytes([0x80, 0x00]) * 80, bytes([0xFF]) * 160),
    "audio/pcma": (bytes([0xAA, 0x2A]) * 80, bytes([0xD5]) * 160),
    "audio/pcm": (b"\x20\x4e\xe0\xb1", b"\x00\x00"),
}


class ScriptedGptLiveService:
    """Hands out connections, and remembers each one it made."""

    def __init__(self, *, answer_audio: bool = True, speech_deltas: int = 3) -> None:
        self.answer_audio = answer_audio
        self.speech_deltas = speech_deltas
        self.connections: list[ScriptedGptLiveConnection] = []
        self.refusals: deque[ConnectionFailedError] = deque()
        # When set, the next session started is refused with this error instead.
        self.start_errors: deque[Mapping[str, Any]] = deque()
        # When false, a session is never started: a service that has stopped answering.
        self.starts = True
        # When false, a close is never finalised.
        self.finalises = True
        self._activity = asyncio.Event()

    async def open(self) -> ScriptedGptLiveConnection:
        """The `ConnectionOpener`. Refuses while refusals are queued."""
        if self.refusals:
            raise self.refusals.popleft()
        connection = ScriptedGptLiveConnection(self)
        self.connections.append(connection)
        self.noticed()
        return connection

    def refuse_next(self, *, retryable: bool, times: int = 1) -> None:
        for _ in range(times):
            self.refusals.append(ConnectionFailedError("refused", retryable=retryable))

    @property
    def current(self) -> ScriptedGptLiveConnection:
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
        # A bound on a wait for something that should already be on its way, so a broken session
        # fails the test rather than hanging the suite.
        async with asyncio.timeout(2):
            while not satisfied():
                self._activity.clear()
                await self._activity.wait()


class ScriptedGptLiveConnection:
    """One connection to the scripted service."""

    def __init__(self, service: ScriptedGptLiveService) -> None:
        self._service = service
        self._outbox: deque[Mapping[str, Any] | ConnectionFailedError | None] = deque()
        self._ready = asyncio.Event()
        self.sent: list[Mapping[str, Any]] = []
        self.closed = False
        # Where this session is on its timeline, in milliseconds.
        self.now_ms = 0
        self._loud, self._silent = _AUDIO["audio/pcmu"]

    # ------------------------------------------------------------------ the connection seam

    async def send(self, event: Mapping[str, Any]) -> None:
        if self.closed:
            raise ConnectionClosedError("the connection is closed")
        self.sent.append(event)
        self._service.noticed()
        event_type = str(event.get("type"))
        match event_type:
            case "session.start":
                wire = event["session"]["audio"]["format"]
                loud, silent = _AUDIO[wire["type"]]
                if wire["type"] == "audio/pcm":
                    # Samples of a 16-bit wave: a pair per two samples, a zero per sample.
                    samples = wire["rate"] * DELTA_MS // 1000
                    loud, silent = loud * (samples // 2), silent * samples
                self._loud, self._silent = loud, silent
                if self._service.start_errors:
                    self._push({"type": "error", "error": self._service.start_errors.popleft()})
                elif self._service.starts:
                    self._push({"type": "session.started", "session": {"id": "sess_example"}})
            case (
                "session.instructions.append"
                | "session.thinking.append"
                | ("session.commentary.append")
            ):
                self._push({"type": event_type.replace("append", "appended")})
            case "session.input_audio.append" if self._service.answer_audio:
                self.reply()
            case "session.close" if self._service.finalises:
                self.close_session("close_requested")

    async def receive(self) -> Mapping[str, Any] | None:
        while not self._outbox:
            if self.closed:
                raise ConnectionClosedError("the connection is closed")
            self._ready.clear()
            await self._ready.wait()
        outgoing = self._outbox.popleft()
        self._service.noticed()
        if isinstance(outgoing, ConnectionFailedError):
            self.closed = True
            raise outgoing
        return outgoing

    async def close(self) -> None:
        self.closed = True
        self._ready.set()

    # ---------------------------------------------------------------- what a test makes happen

    def reply(self, *, speech: int | None = None, silence: int = 0, transcript: str = "") -> None:
        """Queue `speech` pieces of speech, its words, and then `silence` milliseconds of quiet."""
        deltas = self._service.speech_deltas if speech is None else speech
        start = self.now_ms
        for _ in range(deltas):
            self.audio(loud=True)
        words = transcript or REPLY_TRANSCRIPT
        self._push(_fragment("session.output_transcript.delta", words, start, self.now_ms))
        self.silence(silence)

    def audio(self, *, loud: bool) -> None:
        """Queue twenty milliseconds of speech or silence."""
        delta = base64.b64encode(self._loud if loud else self._silent).decode("ascii")
        self._push({"type": "session.output_audio.delta", "delta": delta})
        self.now_ms += DELTA_MS

    def caller_said(self, text: str, *, after_ms: int = 0, lasting_ms: int = 400) -> None:
        """Queue the caller's words, `after_ms` from now, with the silence the service streams."""
        self._words("session.input_transcript.delta", text, after_ms, lasting_ms)

    def assistant_said(self, text: str, *, after_ms: int = 0, lasting_ms: int = 400) -> None:
        """Queue the assistant's words, with silence where its audio would be."""
        self._words("session.output_transcript.delta", text, after_ms, lasting_ms)

    def silence(self, milliseconds: int) -> None:
        """Queue silence: what the service streams while nobody speaks."""
        for _ in range(milliseconds // DELTA_MS):
            self.audio(loud=False)

    def delegate(self, delegation_id: str) -> None:
        self._push(
            {
                "type": "session.delegation.created",
                "offset_ms": self.now_ms,
                "delegation": {"id": delegation_id, "type": "delegation", "target": "client"},
            }
        )

    def close_session(self, reason: str, *, seconds: float | None = USAGE_SECONDS) -> None:
        """Finalise the session and end the connection, as the service does."""
        usage = {} if seconds is None else {"usage": {"seconds": seconds}}
        self._push({"type": "session.closed", "reason": reason, **usage})
        self.hang_up()

    def emit(self, event: Mapping[str, Any]) -> None:
        """Send any event at all, known to the protocol or not."""
        self._push(event)

    def drop(self, *, retryable: bool = True) -> None:
        """Fail the connection once everything already queued has been delivered."""
        self._outbox.append(ConnectionFailedError("dropped", retryable=retryable))
        self._ready.set()

    def hang_up(self) -> None:
        """End the connection from the service's side."""
        self._outbox.append(None)
        self._ready.set()

    def sent_types(self) -> list[str]:
        return [str(event["type"]) for event in self.sent]

    def sent_of(self, event_type: str) -> list[Mapping[str, Any]]:
        return [event for event in self.sent if event["type"] == event_type]

    def appended_instructions(self) -> list[str]:
        return [str(event["content"]) for event in self.sent_of("session.instructions.append")]

    @property
    def pending(self) -> int:
        return len(self._outbox)

    def _words(self, event_type: str, text: str, after_ms: int, lasting_ms: int) -> None:
        self.silence(after_ms)
        start = self.now_ms
        self.silence(lasting_ms)
        self._push(_fragment(event_type, text, start, self.now_ms))

    def _push(self, payload: Mapping[str, Any]) -> None:
        self._outbox.append(payload)
        self._ready.set()


def _fragment(event_type: str, text: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"type": event_type, "delta": text, "start_ms": start_ms, "end_ms": end_ms}
