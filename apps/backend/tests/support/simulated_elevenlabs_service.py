"""An ElevenLabs agent in the test process over a real socket, holding a client to its rules."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs, urlsplit

from websockets.exceptions import ConnectionClosed

from tests.support.simulated_service import SimulatedService, is_speech

if TYPE_CHECKING:
    from collections.abc import Coroutine, Iterable

    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response

SIMULATED_API_KEY: Final = "simulated-key-accepted-by-nothing-but-this-simulation"
SIMULATED_AGENT_ID: Final = "simulated-agent"
SIMULATED_TRANSCRIPT: Final = "simulated transcript"
SIMULATED_REPLY: Final = "simulated reply"

# What an agent sends by default, and every override this adapter needs the agent to allow.
DEFAULT_CLIENT_EVENTS: Final = frozenset(
    {"audio", "interruption", "user_transcript", "agent_response", "ping"}
)
OVERRIDES_THIS_ADAPTER_NEEDS: Final = frozenset({"prompt", "first_message", "language", "voice_id"})

# Close code for a client that broke the rules: a policy the service enforces.
_POLICY_VIOLATION: Final = 1008


@dataclass(frozen=True, slots=True)
class Handshake:
    """What one client presented when it connected."""

    api_key: str | None
    agent_id: str | None


@dataclass(frozen=True, slots=True)
class Opening:
    """What one conversation was opened with."""

    prompt: str | None
    first_message: str | None
    language: str | None
    voice_id: str | None


@dataclass(slots=True)
class _Conversation:
    """The state of one connection: one caller, one conversation."""

    connection: ServerConnection
    ids: count[int] = field(default_factory=lambda: count(1))
    heard: list[bytes] = field(default_factory=list)
    speaking: bool = False
    unanswered_pings: dict[int, float] = field(default_factory=dict)
    reply: asyncio.Task[None] | None = None
    unsent: list[bytes] = field(default_factory=list)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


class SimulatedElevenLabsService(SimulatedService["_Conversation"]):
    """An agent that pings, interrupts itself when talked over, and sends only enabled events."""

    path = "/v1/convai/conversation"

    def __init__(
        self,
        *,
        client_events: Iterable[str] = DEFAULT_CLIENT_EVENTS,
        allowed_overrides: Iterable[str] = OVERRIDES_THIS_ADAPTER_NEEDS,
        ping_interval: float = 0.02,
        pong_deadline: float = 0.25,
    ) -> None:
        super().__init__()
        self._client_events = frozenset(client_events)
        self._allowed_overrides = frozenset(allowed_overrides)
        self._ping_interval = ping_interval
        self._pong_deadline = pong_deadline
        self._hold_after: int | None = None
        self.handshakes: list[Handshake] = []
        self.openings: list[Opening] = []
        self.context_updates: list[str] = []
        # The ids of the pings answered and of the interruptions sent, in order, across every
        # conversation. Ids only mean something within one conversation; tests here hold one.
        self.answered_pings: list[int] = []
        self.interruptions: list[int] = []
        # Whether a reply is stopped where `hold_next_reply` asked.
        self.holding = False

    # -- What a test can make it do ------------------------------------------------------------

    def hold_next_reply(self, *, after: int) -> None:
        """Stop the next reply after `after` chunks, so that speech can interrupt it for certain."""
        self._hold_after = after

    # -- The protocol ----------------------------------------------------------------------------

    def _admit(self, connection: ServerConnection, request: Request) -> Response | None:
        api_key = request.headers.get("xi-api-key")
        agent_id = parse_qs(urlsplit(request.path).query).get("agent_id", [None])[0]
        self.handshakes.append(Handshake(api_key=api_key, agent_id=agent_id))
        if self._refusing or api_key != SIMULATED_API_KEY:
            return connection.respond(401, "invalid api key\n")
        if agent_id != SIMULATED_AGENT_ID:
            return connection.respond(404, "agent not found\n")
        return None

    async def _converse(self, connection: ServerConnection) -> None:
        conversation = _Conversation(connection=connection)
        self._began(connection, conversation)
        try:
            if await self._begin(conversation):
                self._start(conversation, self._ping(conversation))
                async for frame in connection:
                    await self._handle(conversation, frame)
        except ConnectionClosed:
            # The client went away, abruptly or not. Either way this conversation is over, and
            # the cleanup below is the whole of what is left to do about it.
            pass
        finally:
            for task in conversation.tasks:
                task.cancel()
            # Waited on rather than awaited, so a cancellation of this task is not swallowed.
            if conversation.tasks:
                await asyncio.wait(conversation.tasks)
            self._ended(connection)

    async def _begin(self, conversation: _Conversation) -> bool:
        event = json.loads(await conversation.connection.recv())
        if event.get("type") != "conversation_initiation_client_data":
            await conversation.connection.close(_POLICY_VIOLATION, "conversation not opened")
            return False
        override = event.get("conversation_config_override", {})
        agent, tts = override.get("agent", {}), override.get("tts", {})
        present = {
            "prompt": "prompt" in agent,
            "first_message": "first_message" in agent,
            "language": "language" in agent,
            "voice_id": "voice_id" in tts,
        }
        refused = {name for name, sent in present.items() if sent} - self._allowed_overrides
        if refused:
            await self._emit(
                conversation,
                "client_error",
                error_event={"code": _POLICY_VIOLATION, "message": "override not allowed"},
            )
            await conversation.connection.close(_POLICY_VIOLATION, "override not allowed")
            return False
        self.openings.append(
            Opening(
                prompt=agent.get("prompt", {}).get("prompt"),
                first_message=agent.get("first_message"),
                language=agent.get("language"),
                voice_id=tts.get("voice_id"),
            )
        )
        await self._emit(
            conversation,
            "conversation_initiation_metadata",
            conversation_initiation_metadata_event={
                "conversation_id": f"conversation-{len(self.openings)}",
                "user_input_audio_format": "pcm_16000",
                "agent_output_audio_format": "pcm_16000",
            },
        )
        return True

    async def _handle(self, conversation: _Conversation, frame: str | bytes) -> None:
        event = json.loads(frame)
        if "user_audio_chunk" in event:
            await self._hear(conversation, event["user_audio_chunk"])
            return
        match event.get("type"):
            case "pong":
                event_id = event.get("event_id")
                conversation.unanswered_pings.pop(event_id, None)
                self.answered_pings.append(event_id)
            case "contextual_update":
                self.context_updates.append(event.get("text", ""))
            case other:
                await self._emit(
                    conversation,
                    "client_error",
                    error_event={"code": 1003, "message": f"unsupported event {other!r}"},
                )

    async def _hear(self, conversation: _Conversation, encoded: str) -> None:
        try:
            audio = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            await conversation.connection.close(_POLICY_VIOLATION, "audio is not base64")
            return
        if is_speech(audio):
            if not conversation.speaking:
                conversation.speaking = True
                await self._interrupt(conversation)
            conversation.heard.append(audio)
        elif conversation.speaking:
            conversation.speaking = False
            heard, conversation.heard = conversation.heard, []
            await self._send_if_enabled(
                conversation,
                "user_transcript",
                user_transcription_event={"user_transcript": SIMULATED_TRANSCRIPT},
            )
            hold, self._hold_after = self._hold_after, None
            conversation.reply = self._start(conversation, self._reply(conversation, heard, hold))

    async def _reply(
        self, conversation: _Conversation, heard: list[bytes], hold: int | None
    ) -> None:
        await self._send_if_enabled(
            conversation, "agent_response", agent_response_event={"agent_response": SIMULATED_REPLY}
        )
        conversation.unsent = list(heard)
        for index, chunk in enumerate(heard):
            if index == hold:
                # Held for good: only an interruption, or the end of the connection, ends it.
                self.holding = True
                await asyncio.Event().wait()
            await self._audio(conversation, chunk, next(conversation.ids))
            conversation.unsent = heard[index + 1 :]

    async def _interrupt(self, conversation: _Conversation) -> None:
        reply, conversation.reply = conversation.reply, None
        if reply is None or reply.done():
            return
        reply.cancel()
        await asyncio.wait({reply})
        self.holding = False
        on_its_way, interruption = next(conversation.ids), next(conversation.ids)
        self.interruptions.append(interruption)
        await self._send_if_enabled(
            conversation, "interruption", interruption_event={"event_id": interruption}
        )
        if conversation.unsent:
            await self._audio(conversation, conversation.unsent[0], on_its_way)

    async def _ping(self, conversation: _Conversation) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(self._ping_interval)
            overdue = [
                sent
                for sent in conversation.unanswered_pings.values()
                if loop.time() - sent > self._pong_deadline
            ]
            if overdue:
                await conversation.connection.close(_POLICY_VIOLATION, "ping not answered")
                return
            event_id = next(conversation.ids)
            if "ping" in self._client_events:
                conversation.unanswered_pings[event_id] = loop.time()
                await self._emit(
                    conversation, "ping", ping_event={"event_id": event_id, "ping_ms": 1}
                )

    async def _audio(self, conversation: _Conversation, chunk: bytes, event_id: int) -> None:
        await self._send_if_enabled(
            conversation,
            "audio",
            audio_event={"audio_base_64": base64.b64encode(chunk).decode(), "event_id": event_id},
        )

    async def _send_if_enabled(self, conversation: _Conversation, kind: str, **fields: Any) -> None:
        if kind in self._client_events:
            await self._emit(conversation, kind, **fields)

    async def _emit(self, conversation: _Conversation, kind: str, **fields: Any) -> None:
        await conversation.connection.send(json.dumps({"type": kind, **fields}))

    def _start(
        self, conversation: _Conversation, work: Coroutine[Any, Any, None]
    ) -> asyncio.Task[None]:
        task: asyncio.Task[None] = asyncio.create_task(self._quietly(work))
        conversation.tasks.add(task)
        task.add_done_callback(conversation.tasks.discard)
        return task

    @staticmethod
    async def _quietly(work: Coroutine[Any, Any, None]) -> None:
        # A connection closing under a reply or a ping is the end of the conversation, which the
        # conversation's own cleanup is already handling.
        with contextlib.suppress(ConnectionClosed):
            await work
