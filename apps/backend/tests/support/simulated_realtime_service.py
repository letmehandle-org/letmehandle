"""A realtime speech service in the test process over a real socket, echoing the caller's audio."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs, urlsplit

from websockets.exceptions import ConnectionClosed

from tests.support.simulated_service import SimulatedService, is_speech

if TYPE_CHECKING:
    from websockets.asyncio.server import ServerConnection
    from websockets.http11 import Request, Response

# The protocol's default input format is 16-bit PCM at 24 kHz: two bytes a sample, 24 000 a
# second. It is only used to turn bytes heard into the milliseconds the protocol reports.
BYTES_PER_MILLISECOND: Final = 48

SIMULATED_API_KEY: Final = "simulated-key-accepted-by-nothing-but-this-simulation"
SIMULATED_MODEL: Final = "simulated-model"
SIMULATED_TRANSCRIPT: Final = "simulated transcript"
MALFORMED_FRAME: Final = "this frame is not json"


@dataclass(frozen=True, slots=True)
class Handshake:
    """What one client presented when it connected, recorded so a test can check it."""

    authorization: str | None
    model: str | None


@dataclass(slots=True)
class Received:
    """What one connection was sent, in order, recorded so a test can check the client's part."""

    event_types: list[str] = field(default_factory=list)
    # The instructions of every session.update that carried any.
    instructions: list[str] = field(default_factory=list)
    # How many events had been received each time the caller was heard to start speaking, so a
    # test can tell what the client sent after the caller spoke from what it sent before.
    speech_started_after: list[int] = field(default_factory=list)

    def since_speech_started(self, turn: int) -> list[str]:
        """The event types received after the caller began their `turn`th turn, from zero."""
        return self.event_types[self.speech_started_after[turn] :]


@dataclass(slots=True)
class _Conversation:
    """The state of one connection: one caller, one conversation."""

    connection: ServerConnection
    received: Received
    session: dict[str, Any] = field(default_factory=dict)
    heard: list[bytes] = field(default_factory=list)
    user_item: str | None = None
    elapsed_ms: int = 0
    assistant_items: set[str] = field(default_factory=set)
    response: asyncio.Task[None] | None = None
    response_id: str | None = None


def _transcribing(session: dict[str, Any]) -> bool:
    """Whether a session was configured to transcribe what the caller says.

    A real service transcribes nobody who did not ask, and a simulation that did would pass a
    client that never asked and then shows its caller no words.
    """
    audio = session.get("audio")
    audio_input = audio.get("input") if isinstance(audio, dict) else None
    return isinstance(audio_input, dict) and bool(audio_input.get("transcription"))


class SimulatedRealtimeService(SimulatedService["_Conversation"]):
    """A realtime speech service that leaves interrupting a response to the client."""

    path = "/v1/realtime"

    def __init__(
        self,
        *,
        api_key: str = SIMULATED_API_KEY,
        model: str = SIMULATED_MODEL,
        transcript: str = SIMULATED_TRANSCRIPT,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._model = model
        self._transcript = transcript
        self._ids = count(1)
        # Set means responses flow; cleared, a response waits before its next output delta. A
        # test holds one mid-sentence this way, which is the only way to interrupt it
        # deterministically.
        self._flowing = asyncio.Event()
        self._flowing.set()
        self._hold_after: int | None = None
        self.handshakes: list[Handshake] = []
        # One for each connection that got past the handshake, in the order they did.
        self.received: list[Received] = []

    # -- What a test can make it do ------------------------------------------------------------

    def hold_responses(self, *, after_deltas: int = 0) -> None:
        """Stop responses once each has sent `after_deltas` output deltas, until released.

        Holding after at least one is how a test makes sure the caller has heard part of a reply
        when they talk over it, so there is something to truncate.
        """
        self._hold_after = after_deltas

    def release_responses(self) -> None:
        self._hold_after = None
        self._flowing.set()

    async def close_connections(self) -> None:
        """Close every connection normally, the way a service that is finished does."""
        for connection in list(self._conversations):
            await connection.close()

    async def send_malformed_frame(self, frame: str | bytes = MALFORMED_FRAME) -> None:
        """Send every connection a frame that is not a protocol event."""
        for connection in list(self._conversations):
            await connection.send(frame)

    # -- The protocol ----------------------------------------------------------------------------

    def _admit(self, connection: ServerConnection, request: Request) -> Response | None:
        authorization = request.headers.get("Authorization")
        model = parse_qs(urlsplit(request.path).query).get("model", [None])[0]
        self.handshakes.append(Handshake(authorization=authorization, model=model))
        if self._refusing or authorization != f"Bearer {self._api_key}":
            return connection.respond(401, "invalid api key\n")
        if model != self._model:
            # The protocol documents the model as a query parameter; how a real service refuses
            # one it does not have is not specified, so this picks a client error that says so.
            return connection.respond(404, "unknown model\n")
        return None

    async def _converse(self, connection: ServerConnection) -> None:
        received = Received()
        self.received.append(received)
        conversation = _Conversation(
            connection=connection, received=received, session={"model": self._model}
        )
        self._began(connection, conversation)
        try:
            await self._emit(conversation, "session.created", session=conversation.session)
            async for frame in connection:
                await self._handle(conversation, frame)
        except ConnectionClosed:
            # The client went away, abruptly or not. Either way this conversation is over, and
            # the cleanup below is the whole of what is left to do about it.
            pass
        finally:
            await self._stop_response(conversation)
            self._ended(connection)

    async def _handle(self, conversation: _Conversation, frame: str | bytes) -> None:
        try:
            event = json.loads(frame)
        except json.JSONDecodeError:
            await self._error(conversation, "invalid_json", "the frame is not JSON")
            return
        event_type = event.get("type") if isinstance(event, dict) else None
        conversation.received.event_types.append(str(event_type))
        match event_type:
            case "session.update":
                settings = event.get("session", {})
                if "instructions" in settings:
                    conversation.received.instructions.append(settings["instructions"])
                conversation.session.update(settings)
                await self._emit(conversation, "session.updated", session=conversation.session)
            case "input_audio_buffer.append":
                await self._hear(conversation, event.get("audio", ""))
            case "response.cancel":
                await self._cancel(conversation)
            case "conversation.item.truncate":
                await self._truncate(conversation, event)
            case other:
                await self._error(conversation, "unknown_event", f"unsupported event {other!r}")

    async def _hear(self, conversation: _Conversation, encoded: str) -> None:
        try:
            audio = base64.b64decode(encoded, validate=True)
        except binascii.Error:
            await self._error(conversation, "invalid_audio", "audio is not base64")
            return
        start_ms = conversation.elapsed_ms
        conversation.elapsed_ms += len(audio) // BYTES_PER_MILLISECOND
        if is_speech(audio):
            if conversation.user_item is None:
                conversation.user_item = self._id("item")
                received = conversation.received
                received.speech_started_after.append(len(received.event_types))
                await self._emit(
                    conversation,
                    "input_audio_buffer.speech_started",
                    audio_start_ms=start_ms,
                    item_id=conversation.user_item,
                )
            conversation.heard.append(audio)
        elif conversation.user_item is not None:
            await self._end_turn(conversation, audio_end_ms=start_ms)

    async def _end_turn(self, conversation: _Conversation, *, audio_end_ms: int) -> None:
        item_id = conversation.user_item
        heard, conversation.heard, conversation.user_item = conversation.heard, [], None
        await self._emit(
            conversation,
            "input_audio_buffer.speech_stopped",
            audio_end_ms=audio_end_ms,
            item_id=item_id,
        )
        await self._emit(conversation, "input_audio_buffer.committed", item_id=item_id)
        if _transcribing(conversation.session):
            await self._emit(
                conversation,
                "conversation.item.input_audio_transcription.completed",
                item_id=item_id,
                content_index=0,
                transcript=self._transcript,
            )
        await self._stop_response(conversation)
        conversation.response_id = self._id("resp")
        conversation.response = asyncio.create_task(
            self._respond(conversation, conversation.response_id, heard)
        )

    async def _respond(
        self, conversation: _Conversation, response_id: str, heard: list[bytes]
    ) -> None:
        item_id = self._id("item")
        conversation.assistant_items.add(item_id)
        position = {"response_id": response_id, "item_id": item_id, "output_index": 0}
        try:
            await self._emit(
                conversation,
                "response.created",
                response={"id": response_id, "status": "in_progress"},
            )
            for sent, chunk in enumerate(heard):
                if self._hold_after is not None and sent >= self._hold_after:
                    self._flowing.clear()
                await self._flowing.wait()
                await self._emit(
                    conversation,
                    "response.output_audio.delta",
                    **position,
                    content_index=0,
                    delta=base64.b64encode(chunk).decode("ascii"),
                )
            await self._emit(
                conversation,
                "response.output_audio_transcript.delta",
                **position,
                content_index=0,
                delta=self._transcript,
            )
            await self._emit(
                conversation, "response.output_audio.done", **position, content_index=0
            )
            await self._emit(
                conversation, "response.done", response={"id": response_id, "status": "completed"}
            )
        except ConnectionClosed:
            # Nobody is left to speak to. The conversation's own cleanup is already under way.
            return

    async def _cancel(self, conversation: _Conversation) -> None:
        response = conversation.response
        if response is None or response.done():
            await self._error(
                conversation, "response_cancel_not_active", "there is no response to cancel"
            )
            return
        await self._stop_response(conversation)
        await self._emit(
            conversation,
            "response.done",
            response={"id": conversation.response_id, "status": "cancelled"},
        )

    async def _truncate(self, conversation: _Conversation, event: dict[str, Any]) -> None:
        item_id = event.get("item_id")
        if item_id not in conversation.assistant_items:
            await self._error(conversation, "item_not_found", "no assistant item with that id")
            return
        await self._emit(
            conversation,
            "conversation.item.truncated",
            item_id=item_id,
            content_index=event.get("content_index", 0),
            audio_end_ms=event.get("audio_end_ms", 0),
        )

    async def _stop_response(self, conversation: _Conversation) -> None:
        response, conversation.response = conversation.response, None
        if response is not None:
            response.cancel()
            # Waited on rather than awaited, so that a cancellation of this task is not mistaken
            # for the response's own and swallowed with it.
            await asyncio.wait({response})

    async def _error(self, conversation: _Conversation, code: str, message: str) -> None:
        await self._emit(
            conversation,
            "error",
            error={"type": "invalid_request_error", "code": code, "message": message},
        )

    async def _emit(self, conversation: _Conversation, kind: str, **fields: Any) -> None:
        event = {"event_id": self._id("event"), "type": kind, **fields}
        await conversation.connection.send(json.dumps(event))

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids)}"
