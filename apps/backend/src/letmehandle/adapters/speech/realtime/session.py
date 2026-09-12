"""One live conversation with a realtime speech service.

The session owns three things — a connection, a bounded queue of events, and the task reading
the connection into that queue — and releases all three on every way out: a close, a failure,
and a cancellation of whoever was using it.

Backpressure is real and has a cost worth stating. The reader waits for room in the queue before
reading further, so a consumer that stops draining it stops the reader, and the service's own
flow control does the rest; nothing grows. While the reader waits, it is not reading the
service's speech-started signal either, so barge-in on a stalled consumer waits until the
consumer drains. A consumer that plays through an `AudioSink` is not stalled — the sink holds
what is waiting to be played — so this only bites when something downstream is already failing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.audio.conversion import AudioConverter, pcm_duration_ms
from letmehandle.adapters.speech.realtime import protocol
from letmehandle.adapters.speech.realtime.protocol import (
    WIRE_FORMAT,
    AudioDelta,
    CallerStartedSpeaking,
    CallerStoppedSpeaking,
    MalformedEventError,
    ResponseFinished,
    ResponseStarted,
    ServiceError,
    TranscriptDelta,
    TranscriptSettled,
)
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.adapters.speech.session_support.recovery import (
    is_retryable,
    receive,
    replace_connection,
)
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import EventConnectionError
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEnded,
    SpeechEvent,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.adapters.speech.realtime.context import SessionContext
    from letmehandle.adapters.speech.realtime.protocol import Event, Inbound
    from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )
    from letmehandle.domain.models.audio import AudioFormat

_COMPLETED: Final = "completed"


@dataclass(frozen=True, slots=True)
class _Queued:
    """An event waiting for the consumer, with what interruption and delivery need to know.

    `response_id` is set on everything the model produced, which is exactly what an interruption
    discards; the caller's own events are left alone, because the caller did say those words.
    """

    event: SpeechEvent
    response_id: str | None = None
    item_id: str | None = None
    audio_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class _Ended:
    """Nothing more will arrive."""


_END: Final = _Ended()


@dataclass(slots=True)
class _Playback:
    """How much of one item of model audio arrived, and how much the consumer has taken.

    What the consumer has taken is this adapter's best approximation of what was heard. It is an
    overestimate by whatever the consumer's sink still holds unplayed, which is the most it can
    know from here; the consumer discarding its sink on interruption keeps the difference to a
    frame or two.
    """

    response_id: str
    item_id: str
    content_index: int
    received_ms: float = 0.0
    delivered_ms: float = 0.0
    # The words of this audio, once its response completed and they were remembered. Forgotten
    # again if the audio turns out not to have been heard.
    turn: Turn | None = None

    @property
    def unheard(self) -> bool:
        return self.delivered_ms < self.received_ms


@dataclass(frozen=True, slots=True)
class SessionSetup:
    """Everything a session is built from that is not state it accumulates."""

    provider: str
    opener: ConnectionOpener
    input_format: AudioFormat
    output_format: AudioFormat
    reconnect: ReconnectPolicy
    queue_size: int
    timekeeping: Timekeeping


class RealtimeSpeechSession(SpeechSession):
    """A `SpeechSession` over one replaceable realtime connection."""

    def __init__(
        self, setup: SessionSetup, context: SessionContext, telemetry: SessionTelemetry
    ) -> None:
        self._setup = setup
        self._context = context
        self._telemetry = telemetry
        self._queue: asyncio.Queue[_Queued | _Ended] = asyncio.Queue(maxsize=setup.queue_size)
        self._inbound = AudioConverter(setup.input_format, WIRE_FORMAT)
        self._outbound = AudioConverter(WIRE_FORMAT, setup.output_format)
        self._connection: EventConnection | None = None
        # The reader, once started. A list so that closing a session that never started is the
        # same code as closing one that did.
        self._tasks: list[asyncio.Task[None]] = []
        # False while there is no connection worth sending to: before the first opens, while one
        # is being replaced, and after the end.
        self._live = False
        self._closed = False
        self._failure: str | None = None
        self._active_response: str | None = None
        self._latest_response: str | None = None
        self._silenced_response: str | None = None
        self._speaking_response: str | None = None
        self._playback: _Playback | None = None
        self._assistant_turn: Turn | None = None

    # --------------------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Open the first connection and begin reading it. Raises `ProviderError`.

        Not retried: a connection that cannot be opened at all is reported to whoever asked for
        the session, now, rather than retried behind their back while they wait.
        """
        try:
            connection = await self._open()
        except EventConnectionError as error:
            await self._drop_connection()
            raise ProviderError(
                self._setup.provider, str(error), retryable=is_retryable(error)
            ) from error
        except BaseException:
            await self._drop_connection()
            raise
        self._live = True
        self._telemetry.opened()
        self._tasks.append(
            asyncio.create_task(self._read(connection), name=f"{self._setup.provider}-reader")
        )

    async def close(self) -> None:
        """Stop reading, close the connection and end the event stream. Safe to repeat."""
        if self._closed:
            return
        self._closed = True
        self._live = False
        try:
            for task in self._tasks:
                task.cancel()
            # The reader's own cancellation is collected rather than raised; a cancellation of
            # whoever is closing still propagates.
            outcomes = await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            await self._drop_connection()
            self._drain()
            self._queue.put_nowait(_END)
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                # A defect, not a failure of the service: surfaced where somebody will see it.
                raise outcome

    # ------------------------------------------------------------------------------- port

    async def send_audio(self, frame: AudioFrame) -> None:
        self._ensure_usable()
        if frame.format != self._setup.input_format:
            raise ProviderError(
                self._setup.provider,
                f"a frame in {frame.format} reached a session opened for "
                f"{self._setup.input_format}",
                retryable=False,
            )
        audio = self._inbound.convert(frame.data)
        if audio:
            await self._send(protocol.append_audio(audio))

    async def events(self) -> AsyncIterator[SpeechEvent]:
        while True:
            queued = await self._queue.get()
            if isinstance(queued, _Ended):
                # Put back, so that every later iterator ends too instead of waiting forever.
                self._queue.put_nowait(queued)
                return
            if queued.response_id is not None and queued.response_id == self._silenced_response:
                continue
            playback = self._playback
            if playback is not None and queued.item_id == playback.item_id:
                playback.delivered_ms += queued.audio_ms
            yield queued.event

    async def update_context(self, context: str) -> None:
        self._ensure_usable()
        # Kept first, so that a reconnect in progress restores the new context, not the old.
        self._context.instructions = context
        await self._send(protocol.update_instructions(context))

    async def interrupt(self) -> None:
        self._ensure_usable()
        await self._silence()

    # -------------------------------------------------------------------------- the reader

    async def _read(self, connection: EventConnection) -> None:
        try:
            await self._converse(connection)
        except Exception:
            # Whoever is iterating the events would otherwise wait forever for a reader that has
            # gone. They are told the session ended, and `close` raises the defect itself.
            await self._fail("the session stopped unexpectedly")
            raise

    async def _converse(self, connection: EventConnection) -> None:
        while True:
            received = await receive(connection)
            if isinstance(received, EventConnectionError):
                self._telemetry.stream_error(StreamErrorKind.CONNECTION)
                replacement = await self._recover(received)
                if replacement is None:
                    return
                connection = replacement
                continue
            try:
                signal = protocol.parse(received)
            except MalformedEventError:
                # One event the adapter cannot read is not worth a caller's conversation. It is
                # counted, and a server that sends nothing else shows up as a wall of them.
                self._telemetry.stream_error(StreamErrorKind.MALFORMED)
                continue
            if signal is not None:
                await self._handle(signal)

    async def _handle(self, signal: Inbound) -> None:
        match signal:
            case CallerStartedSpeaking():
                if self._model_is_speaking():
                    await self._silence()
                await self._enqueue(SpeechStarted(by_caller=True))
            case CallerStoppedSpeaking():
                self._telemetry.caller_stopped()
                await self._enqueue(SpeechEnded(by_caller=True))
            case ResponseStarted(response_id=response_id):
                self._active_response = self._latest_response = response_id
            case ResponseFinished():
                await self._finish_response(signal)
            case AudioDelta():
                await self._play(signal)
            case TranscriptDelta(speaker=Speaker.CALLER, text=text):
                await self._enqueue(
                    TranscriptProduced(text, speaker_is_caller=True, is_final=False)
                )
            case TranscriptSettled(speaker=Speaker.CALLER, text=text):
                self._context.remember(Turn(Speaker.CALLER, text))
                await self._enqueue(TranscriptProduced(text, speaker_is_caller=True, is_final=True))
            case TranscriptDelta(text=text) | TranscriptSettled(text=text):
                await self._assistant_words(text, is_final=isinstance(signal, TranscriptSettled))
            case ServiceError(code=code) if code != protocol.NOTHING_TO_CANCEL:
                self._telemetry.stream_error(StreamErrorKind.SERVICE)

    async def _play(self, delta: AudioDelta) -> None:
        if delta.response_id == self._silenced_response:
            # The service had already sent this when it was told to stop.
            return
        self._telemetry.model_audio_arrived()
        if delta.response_id != self._speaking_response:
            self._speaking_response = delta.response_id
            await self._enqueue(SpeechStarted(by_caller=False), response_id=delta.response_id)
        playback = self._playback
        if playback is None or playback.item_id != delta.item_id:
            playback = self._playback = _Playback(
                delta.response_id, delta.item_id, delta.content_index
            )
        duration = pcm_duration_ms(delta.audio, WIRE_FORMAT.sample_rate_hz)
        playback.received_ms += duration
        audio = self._outbound.convert(delta.audio)
        if audio:
            await self._enqueue(
                AudioProduced(AudioFrame(audio, self._setup.output_format)),
                response_id=delta.response_id,
                item_id=delta.item_id,
                audio_ms=duration,
            )

    async def _assistant_words(self, text: str, *, is_final: bool) -> None:
        response_id = self._active_response
        if response_id is None or response_id == self._silenced_response:
            return
        if is_final:
            # Remembered only once the response completes: words from a response that was cut
            # off were not all heard, and replaying them would tell the model it said them.
            self._assistant_turn = Turn(Speaker.ASSISTANT, text)
        event = TranscriptProduced(text, speaker_is_caller=False, is_final=is_final)
        await self._enqueue(event, response_id=response_id)

    async def _finish_response(self, finished: ResponseFinished) -> None:
        turn, self._assistant_turn = self._assistant_turn, None
        # Responses do not overlap, so whichever one finished, none is in progress now.
        self._active_response = None
        self._telemetry.silenced()
        if finished.response_id == self._silenced_response:
            return
        if turn is not None and finished.status == _COMPLETED:
            self._context.remember(turn)
            if self._playback is not None and self._playback.response_id == finished.response_id:
                self._playback.turn = turn
        if finished.response_id == self._speaking_response:
            await self._enqueue(SpeechEnded(by_caller=False), response_id=finished.response_id)

    # ------------------------------------------------------------------------ interruption

    def _model_is_speaking(self) -> bool:
        return self._active_response is not None or (
            self._playback is not None and self._playback.unheard
        )

    async def _silence(self) -> None:
        """Stop the model, tell the service what was heard, and drop what was waiting.

        The cancel is sent even when no response is known to be in progress, because the service
        may have begun one this session has not read yet; the error it earns when nothing was in
        progress is expected and not counted.
        """
        if self._model_is_speaking():
            self._telemetry.interrupted()
        self._silenced_response = self._latest_response
        self._assistant_turn = None
        playback, self._playback = self._playback, None
        truncate = playback is not None and (playback.unheard or self._active_response is not None)
        self._discard_model_output()
        self._outbound.reset()
        await self._send(protocol.cancel_response())
        if truncate and playback is not None:
            if playback.turn is not None:
                # The response completed at the service, but the caller did not hear all of it.
                self._context.forget(playback.turn)
            await self._send(
                protocol.truncate(
                    item_id=playback.item_id,
                    content_index=playback.content_index,
                    audio_end_ms=int(playback.delivered_ms),
                )
            )
        if self._active_response is None:
            self._telemetry.silenced()
        # Again, because output may have been queued while the cancel was on its way.
        self._discard_model_output()

    def _discard_model_output(self) -> None:
        kept = [
            queued
            for queued in self._drain()
            if isinstance(queued, _Ended) or queued.response_id is None
        ]
        for queued in kept:
            self._queue.put_nowait(queued)

    def _drain(self) -> list[_Queued | _Ended]:
        drained: list[_Queued | _Ended] = []
        while not self._queue.empty():
            drained.append(self._queue.get_nowait())
        return drained

    # ------------------------------------------------------------------------ reconnection

    async def _recover(self, error: EventConnectionError) -> EventConnection | None:
        """Replace a failed connection, or end the session and return `None`."""
        self._live = False
        await self._drop_connection()
        if not is_retryable(error):
            await self._fail(str(error))
            return None
        self._forget_responses()
        replacement = await replace_connection(
            open_connection=self._open,
            abandon=self._drop_connection,
            policy=self._setup.reconnect,
            timekeeping=self._setup.timekeeping,
            telemetry=self._telemetry,
        )
        if isinstance(replacement, str):
            await self._fail(replacement)
            return None
        self._live = True
        return replacement

    def _forget_responses(self) -> None:
        """A new connection has no responses in progress, whatever the old one had."""
        self._active_response = None
        self._assistant_turn = None
        self._playback = None
        self._outbound.reset()

    async def _open(self) -> EventConnection:
        # Held before it is configured, so that a failure or a cancellation part-way through
        # configuring it still finds it to close.
        connection = self._connection = await self._setup.opener()
        for event in self._context.restoration():
            await connection.send(event)
        return connection

    async def _fail(self, reason: str) -> None:
        self._failure = reason
        await self._enqueue(SessionFailed(reason, retryable=False))
        await self._queue.put(_END)

    # --------------------------------------------------------------------------- plumbing

    async def _enqueue(
        self,
        event: SpeechEvent,
        *,
        response_id: str | None = None,
        item_id: str | None = None,
        audio_ms: float = 0.0,
    ) -> None:
        await self._queue.put(_Queued(event, response_id, item_id, audio_ms))

    async def _send(self, event: Event) -> None:
        """Send if there is a connection to send to.

        A failure here is not raised to whoever called. The reader is waiting on the same
        connection and sees the same failure, and it is the one that reconnects and restores
        context — which includes anything this send was carrying that still matters. Audio sent
        into a dead connection is gone either way: replaying it seconds later would answer a
        caller who has moved on.
        """
        connection = self._connection
        if not self._live or connection is None:
            return
        try:
            await connection.send(event)
        except EventConnectionError:
            return

    async def _drop_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    def _ensure_usable(self) -> None:
        if self._failure is not None:
            raise ProviderError(self._setup.provider, self._failure, retryable=False)
        if self._closed:
            raise ProviderError(self._setup.provider, "the session is closed", retryable=False)
