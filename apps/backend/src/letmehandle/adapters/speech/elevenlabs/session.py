"""One live conversation with an ElevenLabs agent.

The session owns a connection, the events held for its consumer and the task reading one into
the other, and releases all of them on every way out: a close, a failure, and a cancellation of
whoever was using it.

Four things are different from a realtime session, and each comes from the protocol rather than
from a choice made here.

The service decides the audio formats. A conversation is not usable until the service has said
which audio it hears and speaks, so opening a connection includes waiting for that, and the
converters at this adapter's edge are rebuilt from the answer every time a connection opens.

The service pings and expects an answer at once, and interrupts its own agent. Both are acted on
the moment they are read, and reading never waits for the consumer (see `outbox`), so neither is
left behind audio a speaker is still working through.

Nothing can stop the agent speaking. The service stops it when the caller talks over it and says
so; this session then discards what was held and ignores the stale audio still on its way.
`interrupt` from this side can only stop playing: whatever the agent sends is dropped until the
caller's next settled turn or the service's own interruption, and the service goes on believing
it was said. For the same reason interruption-to-silence is not measured here: nothing from the
service confirms a silence this side asked for.

A conversation cannot be resumed. A dropped one is replaced by a new conversation told what it
needs in its prompt (see `context`), and a replacement is only trusted once it has delivered
something: one that begins and then drops at once spends the same attempts as one refused.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.speech.elevenlabs import protocol
from letmehandle.adapters.speech.elevenlabs.protocol import (
    DEFAULT_WIRE_FORMAT,
    AgentAudio,
    AgentCorrected,
    AgentSaid,
    CallerSaid,
    ConversationBegan,
    Interrupted,
    Ping,
    ServiceError,
    ToolRequested,
)
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
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
from letmehandle.domain.models.audio import AudioEncoding, AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.adapters.speech.elevenlabs.context import ConversationContext
    from letmehandle.adapters.speech.elevenlabs.protocol import Inbound
    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )
    from letmehandle.domain.models.audio import AudioFormat
    from letmehandle.domain.ports.speech import SpeechEvent


@dataclass(frozen=True, slots=True)
class SessionSetup:
    """Everything a session is built from that is not state it accumulates."""

    provider: str
    opener: ConnectionOpener
    input_format: AudioFormat
    output_format: AudioFormat
    reconnect: ReconnectPolicy
    audio_ceiling_seconds: float
    initiation_timeout: float
    timekeeping: Timekeeping


class ElevenLabsSpeechSession(SpeechSession):
    """A `SpeechSession` over one replaceable ElevenLabs conversation."""

    def __init__(
        self, setup: SessionSetup, context: ConversationContext, telemetry: SessionTelemetry
    ) -> None:
        self._setup = setup
        self._context = context
        self._telemetry = telemetry
        self._outbox = Outbox(setup.audio_ceiling_seconds)
        # Built for the agent's default formats, and rebuilt from what each conversation says.
        self._inbound = AudioConverter(setup.input_format, DEFAULT_WIRE_FORMAT)
        self._outbound = AudioConverter(DEFAULT_WIRE_FORMAT, setup.output_format)
        self._wire_output = DEFAULT_WIRE_FORMAT
        self._connection: EventConnection | None = None
        self._tasks: list[asyncio.Task[None]] = []
        # False while there is no connection worth sending to: before the first opens, while one
        # is being replaced, and after the end.
        self._live = False
        self._began = False
        self._closed = False
        self._failure: str | None = None
        self._budget = ReconnectBudget()
        self._unproven = False
        # Updates made while no conversation could hear them, sent as soon as one can.
        self._pending_updates: list[str] = []
        # Whether agent audio has arrived since the last turn boundary, which is what makes the
        # next piece of audio the start of speech.
        self._speaking = False
        self._muted = False
        self._interrupted_through: int | None = None
        self._last_reply: Turn | None = None

    # --------------------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Open the first conversation and begin reading it. Raises `ProviderError`.

        Not retried: a conversation that cannot be opened at all is reported to whoever asked
        for the session, now, rather than retried behind their back while they wait.
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
            await self._send(protocol.caller_audio(audio))

    def events(self) -> AsyncIterator[SpeechEvent]:
        return self._outbox.events()

    async def update_context(self, context: str) -> None:
        """Tell the agent something new, as background rather than as new instructions.

        The protocol has no way to replace an agent's prompt mid-conversation. What it has is a
        non-interrupting update the agent takes into account from its next reply on, which is
        enough to say that the user is being reached or has joined. Not a user message, which
        would be the caller's words and answered as them. A reply already on its way was written
        without it, which is why the run tells the agent before it acts rather than after. It is
        kept, so that a replacement conversation is told it
        too; and one made while a replacement is being opened is sent once it has begun, since
        the prompt it was opened with was written before the update existed.
        """
        self._ensure_usable()
        self._context.add_update(context)
        if not self._live:
            self._pending_updates.append(context)
            return
        await self._send(protocol.contextual_update(context))

    async def interrupt(self) -> None:
        self._ensure_usable()
        # The service cannot be told to stop, so what it goes on sending is not played instead —
        # including a reply already on its way that this session has not read yet.
        self._muted = True
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
            if self._unproven:
                # Something arrived after the conversation began: this replacement works.
                self._unproven = False
                self._budget.proven()
            signal = self._parse(received)
            if signal is not None:
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
            case Ping(event_id=event_id):
                await self._send(protocol.pong(event_id))
            case AgentAudio():
                await self._play(signal)
            case Interrupted(event_id=event_id):
                through = self._interrupted_through
                self._interrupted_through = event_id if through is None else max(through, event_id)
                self._muted = self._speaking = False
                self._discard_speech()
                self._outbox.put(SpeechStarted(by_caller=True))
            case CallerSaid(text=text):
                # A settled caller turn is the boundary after which the agent's next reply begins.
                self._muted = self._speaking = False
                self._telemetry.caller_stopped()
                self._context.history.remember(Turn(Speaker.CALLER, text))
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=True, is_final=True))
            case AgentSaid(text=text) if not self._muted:
                # Not marked as speech to discard: what the agent began to say was said.
                self._last_reply = Turn(Speaker.ASSISTANT, text)
                self._context.history.remember(self._last_reply)
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=False, is_final=True))
            case AgentCorrected():
                self._correct(signal)
            case ToolRequested(tool_call_id=tool_call_id, expects_response=True):
                await self._send(protocol.refuse_tool(tool_call_id))
            case ServiceError():
                # Counted and survived. A refusal the service cannot continue past ends the
                # connection as well, and that is handled as the connection failing; one it can
                # continue past is not worth ending a caller's conversation over.
                self._telemetry.stream_error(StreamErrorKind.SERVICE)

    async def _play(self, audio: AgentAudio) -> None:
        # The one place reading waits for the consumer, and only once it holds far more audio
        # than any reply: see `outbox`.
        await self._outbox.room_for_audio()
        through = self._interrupted_through
        if self._muted or (through is not None and audio.event_id <= through):
            return
        self._telemetry.model_audio_arrived()
        if not self._speaking:
            self._speaking = True
            self._outbox.put(SpeechStarted(by_caller=False), spoken=True)
        converted = self._outbound.convert(audio.audio)
        if converted:
            self._outbox.put(
                AudioProduced(AudioFrame(converted, self._setup.output_format)),
                spoken=True,
                audio_seconds=_duration_seconds(audio.audio, self._wire_output),
            )

    def _correct(self, correction: AgentCorrected) -> None:
        """Remember what the agent actually said before it was cut off.

        Only the history is corrected. The words already delivered as a final transcript stay
        delivered: the port has no way to take one back, so a consumer's own record keeps the
        reply as the agent began it.
        """
        reply = self._last_reply
        if reply is None or reply.text != correction.original:
            return
        said = Turn(Speaker.ASSISTANT, correction.said) if correction.said.strip() else None
        self._context.history.replace(reply, said)
        self._last_reply = said

    def _discard_speech(self) -> None:
        """Discard what the agent had produced, and anything of it still on its way in."""
        self._outbox.discard_speech()
        self._outbound.reset()

    # ------------------------------------------------------------------------ reconnection

    async def _recover(self, error: EventConnectionError) -> EventConnection | None:
        """Start a replacement conversation, or end the session and return `None`."""
        self._live = False
        await self._drop_connection()
        if not is_retryable(error):
            self._fail(str(error))
            return None
        # A new conversation has no reply in progress and numbers its events afresh.
        self._speaking = self._muted = False
        self._interrupted_through = None
        self._last_reply = None
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
        # Held before the conversation begins, so that a failure or a cancellation part-way
        # through beginning it still finds it to close.
        connection = self._connection = await self._setup.opener()
        # The opening carries every update made so far; only those made after it are pending.
        self._pending_updates.clear()
        await connection.send(self._context.opening(resuming=self._began))
        began = await self._beginning(connection)
        self._inbound = AudioConverter(self._setup.input_format, began.input_format)
        self._outbound = AudioConverter(began.output_format, self._setup.output_format)
        self._wire_output = began.output_format
        self._began = True
        return connection

    async def _beginning(self, connection: EventConnection) -> ConversationBegan:
        """Wait for the service to accept the conversation, answering its pings meanwhile."""
        try:
            async with asyncio.timeout(self._setup.initiation_timeout):
                while True:
                    received = await receive(connection)
                    if isinstance(received, EventConnectionError):
                        raise received
                    signal = protocol.parse(received)
                    if isinstance(signal, ConversationBegan):
                        return signal
                    if isinstance(signal, Ping):
                        await connection.send(protocol.pong(signal.event_id))
                    elif isinstance(signal, ServiceError):
                        self._telemetry.stream_error(StreamErrorKind.SERVICE)
        except TimeoutError:
            raise ConnectionFailedError(
                "the service did not begin the conversation in time", retryable=True
            ) from None
        except MalformedEventError as error:
            # Audio formats this adapter cannot read would be played as noise; the same agent
            # says the same thing on the next attempt.
            raise ConnectionFailedError(str(error), retryable=False) from None

    async def _go_live(self) -> None:
        self._live = True
        pending, self._pending_updates = self._pending_updates, []
        for update in pending:
            await self._send(protocol.contextual_update(update))

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._outbox.put(SessionFailed(reason, retryable=False))
        self._outbox.end()

    # --------------------------------------------------------------------------- plumbing

    async def _send(self, event: Event) -> None:
        """Send if there is a connection to send to.

        A failure here is not raised to whoever called. The reader is waiting on the same
        connection and sees the same failure, and it is the one that starts a replacement —
        which is told every update kept so far. Audio sent into a dead connection is gone either
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


def _duration_seconds(audio: bytes, audio_format: AudioFormat) -> float:
    """How long audio in one of the protocol's formats lasts: two bytes a sample, or one."""
    width = 2 if audio_format.encoding is AudioEncoding.PCM_S16LE else 1
    return len(audio) / width / audio_format.sample_rate_hz
