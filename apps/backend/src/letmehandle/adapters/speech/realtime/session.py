"""One live conversation with a realtime speech service.

The session owns three things — a connection, the events held for its consumer, and the task
reading one into the other — and releases all three on every way out: a close, a failure, and a
cancellation of whoever was using it.

Reading never waits for the consumer except on the model's audio, and then only once far more of
it is held than any reply (see `outbox`). A consumer playing in real time is seconds behind the
service, and the caller's speech-started signal behind a held reply is acted on the moment it
arrives rather than once the speaker catches up. A consumer that stops taking audio altogether
does stop the reader, and the service's own flow control does the rest; nothing grows.

What the caller heard, which an interruption tells the service so the model does not believe it
said the rest, is counted as the audio the consumer has taken. Audio still held here was not
taken and is discarded, so the count is accurate to within the consumer's own playback buffer:
whatever a sink has accepted and not yet played is counted as heard, and nothing here can know
otherwise or take it back. That is why a sink should accept little more than it is about to play.

A dropped connection is replaced and told what it missed, and a replacement is only trusted once
the service does something on it beyond acknowledging its configuration: one that is accepted
and then drops at once spends the same attempts as one refused.

A response the service fails — out of quota, say — is counted as a service error and survived.
Three in a row end the session with a failure not worth retrying, and a completed response in
between starts the count again. The caller hears nothing from a failed response, and a caller
left in silence turn after turn is worse off than one whose call ends and can be handled some
other way; the same service fails the same way on a new connection, so reconnecting is no remedy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import partial
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
from letmehandle.adapters.speech.session_support.outbox import Outbox
from letmehandle.adapters.speech.session_support.reconnect import ReconnectBudget
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
_FAILED: Final = "failed"
# Failed responses in a row a session survives before it ends. One is a hiccup; three is a
# service that has stopped answering and a caller who has noticed.
_FAILED_RESPONSES_TOLERATED: Final = 3


@dataclass(slots=True)
class _Playback:
    """How much of one item of model audio arrived, and how much the consumer has taken.

    What the consumer has taken is what this adapter counts as heard. It overstates by whatever
    the consumer's sink still holds unplayed, and by nothing else: see the module docstring.
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
    audio_ceiling_seconds: float
    timekeeping: Timekeeping


class RealtimeSpeechSession(SpeechSession):
    """A `SpeechSession` over one replaceable realtime connection."""

    def __init__(
        self, setup: SessionSetup, context: SessionContext, telemetry: SessionTelemetry
    ) -> None:
        self._setup = setup
        self._context = context
        self._telemetry = telemetry
        self._outbox = Outbox(setup.audio_ceiling_seconds)
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
        self._budget = ReconnectBudget()
        self._unproven = False
        # Instructions changed while no connection could hear them, sent as soon as one can.
        self._instructions_pending = False
        self._failed_responses = 0
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
        await self._go_live()
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
            self._outbox.abandon()
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

    def events(self) -> AsyncIterator[SpeechEvent]:
        return self._outbox.events()

    async def update_context(self, context: str) -> None:
        """Replace the instructions, now or as soon as there is a connection to hear them.

        Kept, so that a replacement connection is configured with them. One made while a
        replacement is being configured is sent once it is live, since the configuration already
        on its way was built before the update existed.
        """
        self._ensure_usable()
        self._context.instructions = context
        if not self._live:
            self._instructions_pending = True
            return
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
            self._fail("the session stopped unexpectedly")
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
            if signal is None:
                continue
            if self._unproven and not isinstance(signal, ServiceError):
                # The service acted on this replacement rather than acknowledging or refusing it.
                self._unproven = False
                self._budget.proven()
            await self._handle(signal)
            if self._failure is not None:
                return

    async def _handle(self, signal: Inbound) -> None:
        match signal:
            case CallerStartedSpeaking():
                if self._model_is_speaking():
                    await self._silence()
                self._outbox.put(SpeechStarted(by_caller=True))
            case CallerStoppedSpeaking():
                self._telemetry.caller_stopped()
                self._outbox.put(SpeechEnded(by_caller=True))
            case ResponseStarted(response_id=response_id):
                self._active_response = self._latest_response = response_id
            case ResponseFinished():
                await self._finish_response(signal)
            case AudioDelta():
                await self._play(signal)
            case TranscriptDelta(speaker=Speaker.CALLER, text=text):
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=True, is_final=False))
            case TranscriptSettled(speaker=Speaker.CALLER, text=text):
                self._context.remember(Turn(Speaker.CALLER, text))
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=True, is_final=True))
            case TranscriptDelta(text=text) | TranscriptSettled(text=text):
                self._assistant_words(text, is_final=isinstance(signal, TranscriptSettled))
            case ServiceError(code=code) if code != protocol.NOTHING_TO_CANCEL:
                self._telemetry.stream_error(StreamErrorKind.SERVICE)

    async def _play(self, delta: AudioDelta) -> None:
        # The one place reading waits for the consumer, and only once it holds far more audio
        # than any reply: see `outbox`.
        await self._outbox.room_for_audio()
        if delta.response_id == self._silenced_response:
            # The service had already sent this when it was told to stop.
            return
        self._telemetry.model_audio_arrived()
        if delta.response_id != self._speaking_response:
            self._speaking_response = delta.response_id
            self._outbox.put(SpeechStarted(by_caller=False), spoken=True)
        playback = self._playback
        if playback is None or playback.item_id != delta.item_id:
            playback = self._playback = _Playback(
                delta.response_id, delta.item_id, delta.content_index
            )
        duration = pcm_duration_ms(delta.audio, WIRE_FORMAT.sample_rate_hz)
        playback.received_ms += duration
        audio = self._outbound.convert(delta.audio)
        if audio:
            self._outbox.put(
                AudioProduced(AudioFrame(audio, self._setup.output_format)),
                spoken=True,
                audio_seconds=duration / 1000,
                taken=partial(self._delivered, delta.item_id, duration),
            )

    def _delivered(self, item_id: str, duration_ms: float) -> None:
        """The consumer took a piece of model audio: what this session counts as heard."""
        playback = self._playback
        if playback is not None and playback.item_id == item_id:
            playback.delivered_ms += duration_ms

    def _assistant_words(self, text: str, *, is_final: bool) -> None:
        response_id = self._active_response
        if response_id is None or response_id == self._silenced_response:
            return
        if is_final:
            # Remembered only once the response completes: words from a response that was cut
            # off were not all heard, and replaying them would tell the model it said them.
            self._assistant_turn = Turn(Speaker.ASSISTANT, text)
        event = TranscriptProduced(text, speaker_is_caller=False, is_final=is_final)
        # Discarded with the audio on an interruption: the words of a reply cut off were not said.
        self._outbox.put(event, spoken=True)

    async def _finish_response(self, finished: ResponseFinished) -> None:
        turn, self._assistant_turn = self._assistant_turn, None
        # Responses do not overlap, so whichever one finished, none is in progress now.
        self._active_response = None
        self._telemetry.silenced()
        if finished.status == _COMPLETED:
            self._failed_responses = 0
        elif finished.status == _FAILED:
            self._telemetry.stream_error(StreamErrorKind.SERVICE)
            self._failed_responses += 1
            if self._failed_responses >= _FAILED_RESPONSES_TOLERATED:
                await self._end(f"the service failed {self._failed_responses} responses in a row")
                return
        if finished.response_id == self._silenced_response:
            return
        if turn is not None and finished.status == _COMPLETED:
            self._context.remember(turn)
            if self._playback is not None and self._playback.response_id == finished.response_id:
                self._playback.turn = turn
        if finished.response_id == self._speaking_response:
            self._outbox.put(SpeechEnded(by_caller=False), spoken=True)

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
        self._outbox.discard_speech()
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
        # Again, because output may have been held while the cancel was on its way.
        self._outbox.discard_speech()

    # ------------------------------------------------------------------------ reconnection

    async def _recover(self, error: EventConnectionError) -> EventConnection | None:
        """Replace a failed connection, or end the session and return `None`."""
        self._live = False
        await self._drop_connection()
        if not is_retryable(error):
            self._fail(str(error))
            return None
        self._forget_responses()
        replacement = await replace_connection(
            open_connection=self._open,
            abandon=self._drop_connection,
            policy=self._setup.reconnect,
            timekeeping=self._setup.timekeeping,
            telemetry=self._telemetry,
            budget=self._budget,
        )
        if isinstance(replacement, str):
            self._fail(replacement)
            return None
        self._unproven = True
        await self._go_live()
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
        # The restoration carries the instructions as they are now; only a later change is pending.
        self._instructions_pending = False
        for event in self._context.restoration():
            await connection.send(event)
        return connection

    async def _end(self, reason: str) -> None:
        """End the session over a connection that still works, and let it go."""
        self._fail(reason)
        self._live = False
        await self._drop_connection()

    async def _go_live(self) -> None:
        self._live = True
        if self._instructions_pending:
            self._instructions_pending = False
            await self._send(protocol.update_instructions(self._context.instructions))

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._outbox.put(SessionFailed(reason, retryable=False))
        self._outbox.end()

    # --------------------------------------------------------------------------- plumbing

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
