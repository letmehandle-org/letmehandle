"""One live conversation with GPT-Live.

The session owns a connection, the events held for its consumer and the task reading one into the
other, and releases all of them on every way out: a close, a failure, and a cancellation of whoever
was using it.

The protocol is full duplex, and most of what the port reports is derived here rather than read:

- **The assistant speaking.** Audio arrives continuously, the silence between words included, and
  no event says speech began or ended. `SpeechStarted(by_caller=False)` is emitted when the audio
  turns loud enough to be speech, and `SpeechEnded(by_caller=False)` once it has been quiet for
  `_SPEECH_END_MS`. Time to first audio is measured to the first speech, not the first silence.
- **The caller speaking.** No event says the caller began either. `SpeechStarted(by_caller=True)` is
  emitted with the first words of each utterance written down, and `SpeechEnded(by_caller=True)`
  when the utterance settles. Words are written down a little after they are spoken, so this is
  later than a voice activity signal would be.
- **Barge-in.** The model listens while it speaks and stops when talked over; nothing needs to be
  sent. The consumer drops what it was about to play on the caller's `SpeechStarted`, as it does for
  every provider, which here costs at most the little audio a paced sink holds.
- **Settled words.** Fragments become final transcripts by `turns`. A caller's settled utterance is
  also what decides the language: when it is clearly in another listed language, the model is told,
  in that language, to switch to it and stay (see `language`). It is told as quiet context, which
  does not interrupt the reply it is usually already giving.
- **Interruption from this side.** Nothing tells the service to stop, so `interrupt` drops what was
  held and plays nothing more of the assistant's current speech, until that speech ends or the
  caller begins speaking. The service goes on believing it was said.

The model may ask for help with a delegation. The product's own agent reads the transcript and acts;
the speech layer runs no tools, so every delegation is answered, quietly, that there is no help to
be had and the model should carry on itself.

A dropped connection is replaced by a new session told what it needs (see `context`), and a
replacement is only trusted once it has delivered something: one that starts and then drops at once
spends the same attempts as one refused. A session the service closes over its content is not
replaced, because a replacement would be told the same things.

Closing asks the service to finalise the session and waits, boundedly, for it to say so, because
only its final word carries the voice time the session is billed for.
"""

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
    OutputAudio,
    ServiceError,
    SessionClosed,
    SessionStarted,
    TranscriptFragment,
)
from letmehandle.adapters.speech.gpt_live.turns import TurnAssembler
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.adapters.speech.session_support.history import Speaker
from letmehandle.adapters.speech.session_support.outbox import Outbox
from letmehandle.adapters.speech.session_support.reconnect import ReconnectBudget
from letmehandle.adapters.speech.session_support.recovery import (
    is_retryable,
    receive,
    replace_connection,
)
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import (
    ConnectionFailedError,
    EventConnectionError,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat, AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEnded,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)
from letmehandle.observability import catalogue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from letmehandle.adapters.speech.gpt_live.context import SessionContext
    from letmehandle.adapters.speech.gpt_live.protocol import Inbound
    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.history import Turn
    from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechEvent

# The voice time a finalised session reports, which is what it is billed for.
SESSION_SECONDS: Final = catalogue.measure(
    "speech.session_seconds", provider=catalogue.NAMED_IN_CODE
)

# How loud the assistant's audio must be, as the root mean square of 16-bit samples, to count as
# speech rather than the silence the service streams between words. Far above line noise, far below
# the quietest word.
_SPEECH_LEVEL: Final = 300.0
# How long the assistant's audio stays quiet before its speech has ended. Longer than the pause
# between two sentences of one reply.
_SPEECH_END_MS: Final = 800.0

# What a delegation is told. Words for the model, not a caller.
NO_DELEGATE: Final = (
    "No backend is available to help with this. Do not wait for a result: continue the "
    "conversation yourself, following your instructions."
)

# Words in an error's type or code that describe the service's condition rather than the request,
# and so may have passed by the next attempt.
_PASSING_CONDITIONS: Final = ("rate_limit", "server_error", "overloaded", "unavailable", "timeout")


@dataclass(frozen=True, slots=True)
class SessionSetup:
    """Everything a session is built from that is not state it accumulates."""

    provider: str
    opener: ConnectionOpener
    input_format: AudioFormat
    output_format: AudioFormat
    wire_format: AudioFormat
    languages: Sequence[str]
    reconnect: ReconnectPolicy
    audio_ceiling_seconds: float
    start_timeout: float
    close_timeout: float
    turn_gap_ms: int
    timekeeping: Timekeeping
    metrics: MetricsRecorder


class GptLiveSpeechSession(SpeechSession):
    """A `SpeechSession` over one replaceable GPT-Live session."""

    def __init__(
        self, setup: SessionSetup, context: SessionContext, telemetry: SessionTelemetry
    ) -> None:
        self._setup = setup
        self._context = context
        self._telemetry = telemetry
        self._outbox = Outbox(setup.audio_ceiling_seconds)
        wire = setup.wire_format
        self._inbound = AudioConverter(setup.input_format, wire)
        self._outbound = AudioConverter(wire, setup.output_format)
        self._linear = AudioConverter(
            wire, AudioFormat(AudioEncoding.PCM_S16LE, wire.sample_rate_hz)
        )
        self._connection: EventConnection | None = None
        self._tasks: list[asyncio.Task[None]] = []
        # False while there is no connection worth sending to: before the first opens, while one
        # is being replaced, and after the end.
        self._live = False
        self._began = False
        self._closed = False
        self._closing = False
        self._finalised = asyncio.Event()
        self._failure: str | None = None
        self._budget = ReconnectBudget()
        self._unproven = False
        self._turns = TurnAssembler(setup.turn_gap_ms)
        # How much of the assistant's audio this connection has delivered, which is how far along
        # the session's timeline it is.
        self._timeline_ms = 0.0
        self._speaking = False
        self._quiet_ms = 0.0
        self._muted = False

    # --------------------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Open the first session and begin reading it. Raises `ProviderError`.

        Not retried: a session that cannot be opened at all is reported to whoever asked for it,
        now, rather than retried behind their back while they wait.
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
        """Finalise the session, stop reading, close the connection and end the stream.

        Safe to repeat. Waits for the service's final word only as long as `close_timeout`, and
        releases everything whether or not it came.
        """
        if self._closed:
            return
        self._closed = True
        outcomes: list[BaseException | None] = []
        try:
            if self._live and self._failure is None and self._reading():
                self._closing = True
                await self._send(protocol.close_session())
                try:
                    async with asyncio.timeout(self._setup.close_timeout):
                        await self._finalised.wait()
                except TimeoutError:
                    pass
        finally:
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
        """Tell the model what changed, as instructions added to the ones it has.

        The protocol cannot replace a running session's instructions, so the lines that are new are
        added (see `context`). The whole context is kept, so a replacement session starts with it;
        one made while a replacement is being opened is added once it is live.
        """
        self._ensure_usable()
        self._context.instructions = context
        if self._live:
            await self._catch_up()

    async def interrupt(self) -> None:
        self._ensure_usable()
        # The service cannot be told to stop, so the rest of the assistant's current speech is not
        # played instead — including audio already on its way that this session has not read yet.
        self._muted = True
        self._quiet_ms = 0.0
        self._turns.discard(Speaker.ASSISTANT)
        self._discard_speech()

    # -------------------------------------------------------------------------- the reader

    async def _read(self, connection: EventConnection) -> None:
        try:
            await self._converse(connection)
        except Exception:
            # Whoever is iterating the events would otherwise wait forever for a reader that has
            # gone. They are told the session ended, and `close` raises the defect itself.
            self._fail("the session stopped unexpectedly")
            raise
        finally:
            self._finalised.set()

    async def _converse(self, connection: EventConnection) -> None:
        while True:
            received = await receive(connection)
            if isinstance(received, EventConnectionError):
                if self._closing:
                    return
                self._telemetry.stream_error(StreamErrorKind.CONNECTION)
                replacement = await self._recover(received)
                if replacement is None:
                    return
                connection = replacement
                continue
            signal = self._parse(received)
            if signal is None:
                continue
            if self._unproven and not isinstance(signal, ServiceError | SessionClosed):
                # The service acted on this replacement rather than acknowledging or refusing it.
                self._unproven = False
                self._budget.proven()
            if isinstance(signal, SessionClosed):
                replacement = await self._closed_by_service(signal)
                if replacement is None:
                    return
                connection = replacement
                continue
            await self._handle(signal)

    def _parse(self, event: Event) -> Inbound | None:
        try:
            return protocol.parse(event)
        except MalformedEventError:
            # One event the adapter cannot read is not worth a caller's conversation. It is
            # counted, and a service that sends nothing else shows up as a wall of them.
            self._telemetry.stream_error(StreamErrorKind.MALFORMED)
            return None

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
            case ServiceError():
                # Counted and survived. A refusal the service cannot continue past closes the
                # session as well, and that is handled as the close; one it can continue past —
                # moderation cutting a sentence short, say — is not worth ending a call over.
                self._telemetry.stream_error(StreamErrorKind.SERVICE)
            case _:
                # A second `session.started` says nothing a running session needs.
                pass

    async def _play(self, audio: bytes) -> None:
        # The one place reading waits for the consumer, and only once it holds far more audio
        # than any reply: see `outbox`.
        await self._outbox.room_for_audio()
        duration_ms = _duration_ms(audio, self._setup.wire_format)
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
            # A new utterance. The model yields to it by itself; this side stops holding back.
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
        # Discarded with the audio on an interruption: the words of speech cut off were not said.
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
        spoken = language.spoken_language(text, self._setup.languages)
        if spoken is None or spoken == self._context.language:
            return
        self._context.language = spoken
        # Quiet context rather than instructions: the model is usually answering by now, and an
        # instruction interrupts it, which cut replies off mid-word and left the caller in silence.
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
        """Discard what the assistant had produced, and anything of it still on its way in."""
        self._outbox.discard_speech()
        self._outbound.reset()
        self._speaking = False

    # ------------------------------------------------------------------------ reconnection

    async def _closed_by_service(self, closed: SessionClosed) -> EventConnection | None:
        """The service finalised the session: done, ended for good, or worth another."""
        self._record_usage(closed)
        if self._closing:
            await self._settle(self._turns.flush())
            return None
        if closed.reason == protocol.CONTENT_CLOSE:
            await self._end("the service ended the session over what was said")
            return None
        self._telemetry.stream_error(StreamErrorKind.CONNECTION)
        return await self._recover(
            ConnectionFailedError(
                f"the service closed the session: {closed.reason}", retryable=True
            )
        )

    async def _recover(self, error: EventConnectionError) -> EventConnection | None:
        """Start a replacement session, or end this one and return `None`."""
        self._live = False
        await self._drop_connection()
        # What was said on the old connection was said.
        await self._settle(self._turns.flush())
        if not is_retryable(error):
            self._fail(str(error))
            return None
        # A new session has no speech in progress, whatever the old one had.
        self._muted = self._speaking = False
        self._outbound.reset()
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

    async def _open(self) -> EventConnection:
        # Held before the session starts, so that a failure or a cancellation part-way through
        # starting it still finds it to close.
        connection = self._connection = await self._setup.opener()
        resuming = self._began
        await connection.send(self._context.start(resuming=resuming))
        await self._started(connection)
        for event in self._context.opening(resuming=resuming):
            await connection.send(event)
        self._began = True
        self._turns = TurnAssembler(self._setup.turn_gap_ms)
        self._timeline_ms = self._quiet_ms = 0.0
        self._speaking = False
        self._outbound.reset()
        self._linear.reset()
        return connection

    async def _started(self, connection: EventConnection) -> None:
        """Wait for the service to start the session, or raise why it did not."""
        try:
            async with asyncio.timeout(self._setup.start_timeout):
                while True:
                    received = await receive(connection)
                    if isinstance(received, EventConnectionError):
                        raise received
                    signal = self._parse(received)
                    match signal:
                        case SessionStarted():
                            return
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
                            continue
        except TimeoutError:
            raise ConnectionFailedError(
                "the service did not start the session in time", retryable=True
            ) from None

    async def _go_live(self) -> None:
        self._live = True
        await self._catch_up()

    async def _catch_up(self) -> None:
        for event in self._context.catch_up():
            await self._send(event)

    async def _end(self, reason: str) -> None:
        """End the session over a connection that still works, and let it go."""
        self._fail(reason)
        self._live = False
        await self._drop_connection()

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._outbox.put(SessionFailed(reason, retryable=False))
        self._outbox.end()

    def _record_usage(self, closed: SessionClosed) -> None:
        if closed.seconds is not None:
            labels = {"provider": self._setup.provider}
            self._setup.metrics.observe(SESSION_SECONDS, closed.seconds, labels)

    # --------------------------------------------------------------------------- plumbing

    def _reading(self) -> bool:
        return any(not task.done() for task in self._tasks)

    async def _send(self, event: Event) -> None:
        """Send if there is a connection to send to.

        A failure here is not raised to whoever called. The reader is waiting on the same
        connection and sees the same failure, and it is the one that starts a replacement — which
        starts with the context as it now stands. Audio sent into a dead connection is gone either
        way: replaying it seconds later would answer a caller who has moved on.
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


def _duration_ms(audio: bytes, audio_format: AudioFormat) -> float:
    """How long audio in one of the protocol's formats lasts: two bytes a sample, or one."""
    width = 2 if audio_format.encoding is AudioEncoding.PCM_S16LE else 1
    return len(audio) / width / audio_format.sample_rate_hz * 1000
