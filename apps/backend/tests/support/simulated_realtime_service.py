"""A realtime speech service that runs inside the test process, over a real socket.

A simulation, and it says so. It speaks the subset of the OpenAI Realtime protocol this project
uses, under the protocol's current event names, so that the real client code — the handshake,
the frames, the failures — runs end to end with no account and no network beyond loopback.

What it does not do is understand speech. It has no model and no voice activity detection, so it
stands in for both with rules a test can reason about:

- Audio that is not all zero bytes is speech. The first such chunk of a turn starts speech.
- An all-zero chunk after speech ends the turn: speech stops, the turn is transcribed as the
  fixed transcript it was given, and a response begins.
- The response speaks the caller's own audio back, one output delta per chunk it heard.

A real server interrupts its own response when speech starts, if configured to. This one never
does, because interruption is the client's to prove: it has to cancel the response and truncate
the item itself, and a simulation that did it for the client would hide a client that did not.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from dataclasses import dataclass, field
from itertools import count
from typing import TYPE_CHECKING, Any, Final, Self
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

if TYPE_CHECKING:
    from types import TracebackType

    from websockets.asyncio.server import Server
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
class _Conversation:
    """The state of one connection: one caller, one conversation."""

    connection: ServerConnection
    session: dict[str, Any] = field(default_factory=dict)
    heard: list[bytes] = field(default_factory=list)
    user_item: str | None = None
    elapsed_ms: int = 0
    assistant_items: set[str] = field(default_factory=set)
    response: asyncio.Task[None] | None = None
    response_id: str | None = None


class SimulatedRealtimeService:
    """A realtime speech service on 127.0.0.1 and an ephemeral port, for one test.

    Used as an async context manager. On exit the server is closed and every connection and
    task it started has finished, which is what lets a test count what is left.
    """

    def __init__(
        self,
        *,
        api_key: str = SIMULATED_API_KEY,
        model: str = SIMULATED_MODEL,
        transcript: str = SIMULATED_TRANSCRIPT,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._transcript = transcript
        self._ids = count(1)
        self._server: Server | None = None
        self._conversations: dict[ServerConnection, _Conversation] = {}
        self._refusing = False
        # Set means responses flow; a test clears it to hold a response mid-sentence, which is
        # the only way to interrupt one deterministically.
        self._flowing = asyncio.Event()
        self._flowing.set()
        self._idle = asyncio.Event()
        self._idle.set()
        self.handshakes: list[Handshake] = []

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
        return f"ws://127.0.0.1:{port}/v1/realtime"

    @property
    def open_connections(self) -> int:
        return len(self._conversations)

    async def wait_until_idle(self) -> None:
        """Return once every connection has finished on this side too.

        A client's close completes before the server has run its own cleanup, so a test that
        counts what is left has to wait for the far end rather than guess how long it takes.
        """
        await self._idle.wait()

    # -- What a test can make it do ------------------------------------------------------------

    def refuse_authentication(self) -> None:
        """Answer every later handshake with 401, whatever key it presents."""
        self._refusing = True

    def hold_responses(self) -> None:
        """Stop responses between one output delta and the next, until released."""
        self._flowing.clear()

    def release_responses(self) -> None:
        self._flowing.set()

    def drop_connections(self) -> None:
        """Cut every connection with no close frame, the way a network failure does."""
        for connection in self._conversations:
            connection.transport.abort()

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
        conversation = _Conversation(connection=connection, session={"model": self._model})
        self._conversations[connection] = conversation
        self._idle.clear()
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
            del self._conversations[connection]
            if not self._conversations:
                self._idle.set()

    async def _handle(self, conversation: _Conversation, frame: str | bytes) -> None:
        try:
            event = json.loads(frame)
        except json.JSONDecodeError:
            await self._error(conversation, "invalid_json", "the frame is not JSON")
            return
        match event.get("type") if isinstance(event, dict) else None:
            case "session.update":
                conversation.session.update(event.get("session", {}))
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
        if any(audio):
            if conversation.user_item is None:
                conversation.user_item = self._id("item")
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
            for chunk in heard:
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

    def _require_server(self) -> Server:
        if self._server is None:
            raise RuntimeError("the simulated service is used as an async context manager")
        return self._server
