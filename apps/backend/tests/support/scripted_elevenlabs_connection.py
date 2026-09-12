"""An ElevenLabs agent in memory, speaking the protocol and nothing else.

It behaves the way the session needs an agent to behave, rather than recording calls for a test to
inspect: it begins a conversation only once it has been opened with one, says which audio formats
the conversation uses, answers caller audio with a spoken reply, and can ping, interrupt its own
agent, drop a connection or refuse one outright.

Like the real service, it never stops a reply because the client asked — there is no way to ask —
and it sends caller transcripts only when a test says the caller spoke.

The audio it speaks is synthetic silence generated here, never a recording (D-013).
"""

from __future__ import annotations

import asyncio
import base64
import itertools
from collections import deque
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

# Twenty milliseconds of 16-bit audio at 16 kHz, the agent's default format.
CHUNK_BYTES: Final = 640
REPLY_WORDS: Final = "happy to help"

type _Outgoing = Mapping[str, Any] | ConnectionFailedError | None


class ScriptedElevenLabsService:
    """Hands out connections, and remembers each one it made."""

    def __init__(
        self,
        *,
        answer_audio: bool = True,
        chunks_per_reply: int = 3,
        input_format: str = "pcm_16000",
        output_format: str = "pcm_16000",
        begins: bool = True,
    ) -> None:
        self.answer_audio = answer_audio
        self.chunks_per_reply = chunks_per_reply
        self.input_format = input_format
        self.output_format = output_format
        self.begins = begins
        # When set, each conversation begins and then ends at once: a service that accepts
        # connections it cannot keep.
        self.hangs_up_after_beginning = False
        self.connections: list[ScriptedElevenLabsConnection] = []
        self.refusals: deque[ConnectionFailedError] = deque()
        # When set, every send waits for it: a service that has stopped answering mid-handshake.
        self.stalled: asyncio.Event | None = None
        self._activity = asyncio.Event()

    async def open(self) -> ScriptedElevenLabsConnection:
        """The `ConnectionOpener`. Refuses while refusals are queued."""
        if self.refusals:
            raise self.refusals.popleft()
        connection = ScriptedElevenLabsConnection(self)
        self.connections.append(connection)
        self.noticed()
        return connection

    def refuse_next(self, *, retryable: bool, times: int = 1) -> None:
        for _ in range(times):
            self.refusals.append(ConnectionFailedError("refused", retryable=retryable))

    @property
    def current(self) -> ScriptedElevenLabsConnection:
        return self.connections[-1]

    @property
    def open_connections(self) -> int:
        return sum(1 for connection in self.connections if not connection.closed)

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
        await self.wait_until(lambda: len(self.connections) >= count)

    async def wait_until(self, satisfied: Callable[[], bool]) -> None:
        # A bound on a wait for something that should already be on its way, so a broken session
        # fails the test rather than hanging the suite.
        async with asyncio.timeout(2):
            while not satisfied():
                self._activity.clear()
                await self._activity.wait()


class ScriptedElevenLabsConnection:
    """One connection to the scripted agent: one conversation."""

    def __init__(self, service: ScriptedElevenLabsService) -> None:
        self._service = service
        self._outbox: deque[_Outgoing] = deque()
        self._ready = asyncio.Event()
        self._event_ids = itertools.count(1)
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
        if event.get("type") == "conversation_initiation_client_data" and self._service.begins:
            self.begin()
            if self._service.hangs_up_after_beginning:
                self.hang_up()
        elif "user_audio_chunk" in event and self._service.answer_audio:
            self.reply()

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

    def begin(self) -> None:
        self._push(
            {
                "type": "conversation_initiation_metadata",
                "conversation_initiation_metadata_event": {
                    "conversation_id": "conversation-scripted",
                    "user_input_audio_format": self._service.input_format,
                    "agent_output_audio_format": self._service.output_format,
                },
            }
        )

    def reply(self, *, chunks: int | None = None, words: str = REPLY_WORDS) -> list[int]:
        """Queue one spoken reply, and return the event ids of its audio."""
        self._push({"type": "agent_response", "agent_response_event": {"agent_response": words}})
        count = self._service.chunks_per_reply if chunks is None else chunks
        return [self.audio() for _ in range(count)]

    def audio(self, *, size: int = CHUNK_BYTES, event_id: int | None = None) -> int:
        """Queue one piece of agent audio, and return its event id."""
        number = next(self._event_ids) if event_id is None else event_id
        encoded = base64.b64encode(bytes(size)).decode("ascii")
        self._push({"type": "audio", "audio_event": {"audio_base_64": encoded, "event_id": number}})
        return number

    def interrupt(self) -> int:
        """The agent is talked over and stops, as the service decides for itself."""
        number = next(self._event_ids)
        self._push({"type": "interruption", "interruption_event": {"event_id": number}})
        return number

    def correct(self, original: str, said: str) -> None:
        self._push(
            {
                "type": "agent_response_correction",
                "agent_response_correction_event": {
                    "original_agent_response": original,
                    "corrected_agent_response": said,
                },
            }
        )

    def caller_said(self, text: str) -> None:
        self._push(
            {"type": "user_transcript", "user_transcription_event": {"user_transcript": text}}
        )

    def ping(self) -> int:
        number = next(self._event_ids)
        self._push({"type": "ping", "ping_event": {"event_id": number, "ping_ms": 20}})
        return number

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
        return [str(event.get("type", "user_audio_chunk")) for event in self.sent]

    def sent_of(self, event_type: str) -> list[Mapping[str, Any]]:
        return [event for event in self.sent if event.get("type") == event_type]

    @property
    def opening(self) -> Mapping[str, Any]:
        """What the conversation was opened with."""
        return self.sent_of("conversation_initiation_client_data")[0]

    @property
    def pending(self) -> int:
        return len(self._outbox)

    # ------------------------------------------------------------------------------ internals

    def _push(self, payload: Mapping[str, Any]) -> None:
        self._outbox.append(payload)
        self._ready.set()
