"""One live conversation with GPT-Live, deriving speech boundaries and settled turns (D-040)."""

from __future__ import annotations

import asyncio
import sys
from array import array
from dataclasses import dataclass
from math import sqrt
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.speech.gpt_live import language, protocol
from letmehandle.adapters.speech.gpt_live.protocol import (
    DelegationRequested,
    Inbound,
    OutputAudio,
    ServiceError,
    SessionClosed,
    SessionStarted,
    TranscriptFragment,
)
from letmehandle.adapters.speech.gpt_live.turns import TurnAssembler
from letmehandle.adapters.speech.session_support.history import Speaker
from letmehandle.adapters.speech.session_support.streaming import StreamingSpeechSession
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import ConnectionFailedError
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat, AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SpeechEnded,
    SpeechStarted,
    TranscriptProduced,
)
from letmehandle.observability import catalogue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.adapters.speech.gpt_live.context import SessionContext
    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.history import Turn
    from letmehandle.adapters.speech.session_support.streaming import SessionSetup
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.websocket.connection import (
        EventConnection,
        EventConnectionError,
    )
    from letmehandle.domain.ports.metrics import MetricsRecorder

# The voice time a finalised session reports, which is what it is billed for.
SESSION_SECONDS: Final = catalogue.measure(
    "speech.session_seconds", provider=catalogue.NAMED_IN_CODE
)

# The RMS of 16-bit samples above which the assistant's audio is speech rather than silence.
_SPEECH_LEVEL: Final = 300.0
# How long the assistant's audio stays quiet before its speech has ended.
_SPEECH_END_MS: Final = 800.0

# What a delegation is told: words for the model, not a caller.
NO_DELEGATE: Final = (
    "No backend is available to help with this. Do not wait for a result: continue the "
    "conversation yourself, following your instructions."
)

# Words in an error's type or code naming a condition that may pass by the next attempt.
_PASSING_CONDITIONS: Final = ("rate_limit", "server_error", "overloaded", "unavailable", "timeout")


@dataclass(frozen=True, slots=True)
class LiveOptions:
    """What a GPT-Live session needs beyond the setup every session shares."""

    wire_format: AudioFormat
    languages: Sequence[str]
    start_timeout: float
    close_timeout: float
    turn_gap_ms: int
    metrics: MetricsRecorder


class GptLiveSpeechSession(StreamingSpeechSession[Inbound]):
    """A `SpeechSession` over one replaceable GPT-Live session, finalised on close."""

    def __init__(
        self,
        setup: SessionSetup,
        context: SessionContext,
        telemetry: SessionTelemetry,
        options: LiveOptions,
    ) -> None:
        super().__init__(setup, telemetry, wire_format=options.wire_format)
        self._context = context
        self._options = options
        wire = options.wire_format
        self._linear = AudioConverter(
            wire, AudioFormat(AudioEncoding.PCM_S16LE, wire.sample_rate_hz)
        )
        self._began = False
        self._closing = False
        self._turns = TurnAssembler(options.turn_gap_ms)
        # How much of the assistant's audio this connection delivered: its place on the timeline.
        self._timeline_ms = 0.0
        self._speaking = False
        self._quiet_ms = 0.0
        self._muted = False

    async def update_context(self, context: str) -> None:
        """Add the lines that are new to the running instructions, now or once live again."""
        self._ensure_usable()
        self._context.instructions = context
        if self._live:
            await self._catch_up()

    async def interrupt(self) -> None:
        """Stop playing the assistant's current speech; the service cannot be told to stop."""
        self._ensure_usable()
        self._muted = True
        self._quiet_ms = 0.0
        self._turns.discard(Speaker.ASSISTANT)
        self._discard_speech()

    # ------------------------------------------------------------------------------ protocol

    def _audio_event(self, audio: bytes) -> Event:
        return protocol.append_audio(audio)

    def _parse_event(self, event: Event) -> Inbound | None:
        return protocol.parse(event)

    def _proves(self, signal: Inbound) -> bool:
        return not isinstance(signal, ServiceError | SessionClosed)

    async def _introduce(self, connection: EventConnection) -> None:
        resuming = self._began
        await connection.send(self._context.start(resuming=resuming))
        await self._read_until(
            connection,
            seconds=self._options.start_timeout,
            late="the service did not start the session in time",
            step=self._starting,
        )
        for event in self._context.opening(resuming=resuming):
            await connection.send(event)
        self._began = True
        self._turns = TurnAssembler(self._options.turn_gap_ms)
        self._timeline_ms = self._quiet_ms = 0.0
        self._speaking = False
        self._outbound.reset()
        self._linear.reset()

    async def _starting(self, event: Event) -> bool | None:
        """`True` once the service starts the session; raises why it did not."""
        match self._parse(event):
            case SessionStarted():
                return True
            case ServiceError(code=code, error_type=error_type):
                self._telemetry.stream_error(StreamErrorKind.SERVICE)
                described = " ".join(each for each in (error_type, code) if each)
                raise ConnectionFailedError(
                    f"the service refused the session: {described or 'unexplained'}",
                    retryable=any(word in described for word in _PASSING_CONDITIONS),
                )
            case SessionClosed(reason=reason):
                raise ConnectionFailedError(
                    f"the service closed the session as it started: {reason}",
                    retryable=reason != protocol.CONTENT_CLOSE,
                )
            case _:
                return None

    async def _catch_up(self) -> None:
        for event in self._context.catch_up():
            await self._send(event)

    async def _forget_connection(self) -> None:
        # What was said on the old connection was said.
        await self._settle(self._turns.flush())
        self._muted = self._speaking = False
        self._outbound.reset()

    async def _finish(self) -> None:
        """Ask the service to finalise the session and wait, boundedly, for its final word."""
        reading = {reader for reader in self._readers if not reader.done()}
        if not self._live or self._failure is not None or not reading:
            return
        self._closing = True
        await self._send(protocol.close_session())
        await asyncio.wait(reading, timeout=self._options.close_timeout)

    async def _connection_failed(self, error: EventConnectionError) -> None:
        if self._closing:
            await self._drop_connection()
            return
        await super()._connection_failed(error)

    async def _handle(self, signal: Inbound) -> None:
        match signal:
            case OutputAudio(audio=audio):
                await self._play(audio)
            case TranscriptFragment(speaker=Speaker.CALLER):
                await self._caller_words(signal)
            case TranscriptFragment():
                await self._assistant_words(signal)
            case DelegationRequested(delegation_id=delegation_id):
                await self._send(protocol.append_thinking(NO_DELEGATE, delegation_id=delegation_id))
            case SessionClosed():
                await self._closed_by_service(signal)
            case ServiceError():
                # Counted and survived; a refusal the service cannot continue past also closes.
                self._telemetry.stream_error(StreamErrorKind.SERVICE)
            case _:
                # A second `session.started` says nothing a running session needs.
                pass

    async def _closed_by_service(self, closed: SessionClosed) -> None:
        """The service finalised the session: done, ended for good, or worth replacing."""
        if closed.seconds is not None:
            labels = {"provider": self._setup.provider}
            self._options.metrics.observe(SESSION_SECONDS, closed.seconds, labels)
        if self._closing:
            await self._settle(self._turns.flush())
            await self._drop_connection()
            return
        if closed.reason == protocol.CONTENT_CLOSE:
            await self._end("the service ended the session over what was said")
            return
        await self._connection_failed(
            ConnectionFailedError(
                f"the service closed the session: {closed.reason}", retryable=True
            )
        )

    # ------------------------------------------------------------------------------- speech

    async def _play(self, audio: bytes) -> None:
        await self._outbox.room_for_audio()
        duration_ms = _duration_ms(audio, self._options.wire_format)
        self._timeline_ms += duration_ms
        await self._settle(self._turns.advance(self._timeline_ms))
        loud = self._is_speech(audio)
        self._quiet_ms = 0.0 if loud else self._quiet_ms + duration_ms
        if self._muted:
            if self._quiet_ms >= _SPEECH_END_MS:
                # The speech this side interrupted is over; what the model says next is heard.
                self._muted = False
                self._outbound.reset()
            return
        if loud and not self._speaking:
            self._speaking = True
            self._telemetry.model_audio_arrived()
            self._outbox.put(SpeechStarted(by_caller=False), spoken=True)
        converted = self._outbound.convert(audio)
        if converted:
            self._outbox.put(
                AudioProduced(AudioFrame(converted, self._setup.output_format)),
                spoken=True,
                audio_seconds=duration_ms / 1000,
            )
        if self._speaking and self._quiet_ms >= _SPEECH_END_MS:
            self._speaking = False
            self._outbox.put(SpeechEnded(by_caller=False), spoken=True)

    async def _caller_words(self, fragment: TranscriptFragment) -> None:
        was_speaking = self._turns.is_speaking(Speaker.CALLER)
        settled = self._turns.fragment(fragment)
        await self._settle(settled)
        if not was_speaking or any(turn.speaker is Speaker.CALLER for turn in settled):
            # A new utterance, which the model yields to by itself.
            self._muted = False
            self._outbox.put(SpeechStarted(by_caller=True))
        self._telemetry.caller_stopped()
        self._outbox.put(TranscriptProduced(fragment.text, speaker_is_caller=True, is_final=False))

    async def _assistant_words(self, fragment: TranscriptFragment) -> None:
        if self._muted:
            # Words of speech nobody hears were not said.
            await self._settle(self._turns.advance(fragment.end_ms))
            return
        await self._settle(self._turns.fragment(fragment))
        event = TranscriptProduced(fragment.text, speaker_is_caller=False, is_final=False)
        self._outbox.put(event, spoken=True)

    async def _settle(self, turns: list[Turn]) -> None:
        """Report settled turns, and follow the caller into another language."""
        for turn in turns:
            by_caller = turn.speaker is Speaker.CALLER
            self._context.history.remember(turn)
            if by_caller:
                self._outbox.put(SpeechEnded(by_caller=True))
            self._outbox.put(
                TranscriptProduced(turn.text, speaker_is_caller=by_caller, is_final=True)
            )
            if by_caller:
                await self._follow_language(turn.text)

    async def _follow_language(self, text: str) -> None:
        spoken = language.spoken_language(text, self._options.languages)
        if spoken is None or spoken == self._context.language:
            return
        self._context.language = spoken
        # Quiet context, since an instruction would interrupt the reply already under way.
        await self._send(protocol.append_thinking(language.switch(spoken)))

    def _is_speech(self, audio: bytes) -> bool:
        linear = self._linear.convert(audio)
        samples = array("h", linear[: len(linear) - len(linear) % 2])
        if sys.byteorder == "big":  # pragma: no cover - every supported host is little-endian
            samples.byteswap()
        if not samples:
            return False
        return sqrt(sum(sample * sample for sample in samples) / len(samples)) >= _SPEECH_LEVEL

    def _discard_speech(self) -> None:
        self._outbox.discard_speech()
        self._outbound.reset()
        self._speaking = False


def _duration_ms(audio: bytes, audio_format: AudioFormat) -> float:
    """How long audio in one of the protocol's formats lasts: two bytes a sample, or one."""
    width = 2 if audio_format.encoding is AudioEncoding.PCM_S16LE else 1
    return len(audio) / width / audio_format.sample_rate_hz * 1000
