"""One live conversation with a service speaking the OpenAI Realtime-compatible protocol."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.audio.conversion import pcm_duration_ms
from letmehandle.adapters.speech.realtime import protocol
from letmehandle.adapters.speech.realtime.protocol import (
    WIRE_FORMAT,
    AudioDelta,
    CallerStartedSpeaking,
    CallerStoppedSpeaking,
    Inbound,
    ResponseFinished,
    ResponseStarted,
    ServiceError,
    TranscriptDelta,
    TranscriptSettled,
)
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.adapters.speech.session_support.streaming import StreamingSpeechSession
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.domain.models.audio import AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SpeechEnded,
    SpeechStarted,
    TranscriptProduced,
)

if TYPE_CHECKING:
    from letmehandle.adapters.speech.realtime.context import SessionContext
    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.streaming import SessionSetup
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.websocket.connection import EventConnection

_COMPLETED: Final = "completed"
_FAILED: Final = "failed"
# Failed responses in a row a session survives before it ends.
_FAILED_RESPONSES_TOLERATED: Final = 3


@dataclass(slots=True)
class _Playback:
    """How much of one item of model audio arrived, and how much the consumer took (heard)."""

    response_id: str
    item_id: str
    content_index: int
    received_ms: float = 0.0
    delivered_ms: float = 0.0
    # The words of this audio once its response completed, forgotten again if it goes unheard.
    turn: Turn | None = None

    @property
    def unheard(self) -> bool:
        return self.delivered_ms < self.received_ms


class RealtimeSpeechSession(StreamingSpeechSession[Inbound]):
    """A `SpeechSession` that cancels what the caller talks over and restores a replacement."""

    def __init__(
        self, setup: SessionSetup, context: SessionContext, telemetry: SessionTelemetry
    ) -> None:
        super().__init__(setup, telemetry, wire_format=WIRE_FORMAT)
        self._context = context
        self._instructions_pending = False
        self._failed_responses = 0
        self._active_response: str | None = None
        self._latest_response: str | None = None
        self._silenced_response: str | None = None
        self._speaking_response: str | None = None
        self._playback: _Playback | None = None
        self._assistant_turn: Turn | None = None

    async def update_context(self, context: str) -> None:
        """Replace the instructions now, or as soon as a connection is live to hear them."""
        self._ensure_usable()
        self._context.instructions = context
        if not self._live:
            self._instructions_pending = True
            return
        await self._send(protocol.update_instructions(context))

    async def interrupt(self) -> None:
        self._ensure_usable()
        await self._silence()

    # ------------------------------------------------------------------------------ protocol

    def _audio_event(self, audio: bytes) -> Event:
        return protocol.append_audio(audio)

    def _parse_event(self, event: Event) -> Inbound | None:
        return protocol.parse(event)

    def _proves(self, signal: Inbound) -> bool:
        return not isinstance(signal, ServiceError)

    async def _introduce(self, connection: EventConnection) -> None:
        # The restoration carries the instructions as they are now.
        self._instructions_pending = False
        for event in self._context.restoration():
            await connection.send(event)

    async def _catch_up(self) -> None:
        if self._instructions_pending:
            self._instructions_pending = False
            await self._send(protocol.update_instructions(self._context.instructions))

    async def _forget_connection(self) -> None:
        self._active_response = None
        self._assistant_turn = None
        self._playback = None
        self._outbound.reset()

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

    # ------------------------------------------------------------------------------- speech

    async def _play(self, delta: AudioDelta) -> None:
        await self._outbox.room_for_audio()
        if delta.response_id == self._silenced_response:
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
        """The consumer took a piece of model audio, which this session counts as heard."""
        playback = self._playback
        if playback is not None and playback.item_id == item_id:
            playback.delivered_ms += duration_ms

    def _assistant_words(self, text: str, *, is_final: bool) -> None:
        response_id = self._active_response
        if response_id is None or response_id == self._silenced_response:
            return
        if is_final:
            # Remembered only once its response completes, since a cut-off reply was not all heard.
            self._assistant_turn = Turn(Speaker.ASSISTANT, text)
        self._outbox.put(
            TranscriptProduced(text, speaker_is_caller=False, is_final=is_final), spoken=True
        )

    async def _finish_response(self, finished: ResponseFinished) -> None:
        turn, self._assistant_turn = self._assistant_turn, None
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

    # ------------------------------------------------------------------------- interruption

    def _model_is_speaking(self) -> bool:
        return self._active_response is not None or (
            self._playback is not None and self._playback.unheard
        )

    async def _silence(self) -> None:
        """Cancel whatever may be in progress, truncate what went unheard, and drop what waits."""
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
        # Again, for output held while the cancel was on its way.
        self._outbox.discard_speech()
