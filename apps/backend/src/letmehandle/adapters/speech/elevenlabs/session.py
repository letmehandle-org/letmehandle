"""One live conversation with an ElevenLabs agent, which the service alone can interrupt."""

from __future__ import annotations

from functools import partial
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
    Inbound,
    Interrupted,
    Ping,
    ServiceError,
    ToolRequested,
)
from letmehandle.adapters.speech.session_support.audio import duration_ms
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.adapters.speech.session_support.streaming import StreamingSpeechSession
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import ConnectionFailedError
from letmehandle.domain.models.audio import AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SpeechStarted,
    TranscriptProduced,
)

if TYPE_CHECKING:
    from letmehandle.adapters.speech.elevenlabs.context import ConversationContext
    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.streaming import SessionSetup
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.websocket.connection import EventConnection


class ElevenLabsSpeechSession(StreamingSpeechSession[Inbound]):
    """A `SpeechSession` whose replacement conversations are told in their prompt what was said."""

    def __init__(
        self,
        setup: SessionSetup,
        context: ConversationContext,
        telemetry: SessionTelemetry,
        *,
        initiation_timeout: float,
    ) -> None:
        super().__init__(setup, telemetry, wire_format=DEFAULT_WIRE_FORMAT)
        self._context = context
        self._initiation_timeout = initiation_timeout
        self._wire_output = DEFAULT_WIRE_FORMAT
        self._began = False
        self._pending_updates: list[str] = []
        # Whether agent audio arrived since the last turn boundary.
        self._speaking = False
        self._muted = False
        self._interrupted_through: int | None = None
        self._last_reply: Turn | None = None

    async def update_context(self, context: str) -> None:
        """Tell the agent something new as a non-interrupting update, kept for a replacement."""
        self._ensure_usable()
        self._context.add_update(context)
        if not self._live:
            self._pending_updates.append(context)
            return
        await self._send(protocol.contextual_update(context))

    async def interrupt(self) -> None:
        """Stop playing what the agent says until its next turn; the service cannot be stopped."""
        self._ensure_usable()
        self._muted = True
        self._discard_speech()

    # ------------------------------------------------------------------------------ protocol

    def _audio_event(self, audio: bytes) -> Event:
        return protocol.caller_audio(audio)

    def _parse_event(self, event: Event) -> Inbound | None:
        return protocol.parse(event)

    async def _introduce(self, connection: EventConnection) -> None:
        # The opening carries every update made so far.
        self._pending_updates.clear()
        await connection.send(self._context.opening(resuming=self._began))
        began = await self._read_until(
            connection,
            seconds=self._initiation_timeout,
            late="the service did not begin the conversation in time",
            step=partial(self._beginning, connection),
        )
        self._inbound = AudioConverter(self._setup.input_format, began.input_format)
        self._outbound = AudioConverter(began.output_format, self._setup.output_format)
        self._wire_output = began.output_format
        self._began = True

    async def _beginning(
        self, connection: EventConnection, event: Event
    ) -> ConversationBegan | None:
        """The conversation's formats once the service accepts it, answering pings meanwhile."""
        try:
            signal = protocol.parse(event)
        except MalformedEventError as error:
            if error.event_type != protocol.INITIATION_METADATA:
                self._telemetry.stream_error(StreamErrorKind.MALFORMED)
                return None
            # The same agent names the same unreadable formats on the next attempt.
            raise ConnectionFailedError(str(error), retryable=False) from None
        if isinstance(signal, Ping):
            await connection.send(protocol.pong(signal.event_id))
        elif isinstance(signal, ServiceError):
            self._telemetry.stream_error(StreamErrorKind.SERVICE)
        return signal if isinstance(signal, ConversationBegan) else None

    async def _catch_up(self) -> None:
        pending, self._pending_updates = self._pending_updates, []
        for update in pending:
            await self._send(protocol.contextual_update(update))

    async def _forget_connection(self) -> None:
        # A new conversation has no reply in progress and numbers its events afresh.
        self._speaking = self._muted = False
        self._interrupted_through = None
        self._last_reply = None

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
                self._muted = self._speaking = False
                self._telemetry.caller_stopped()
                self._context.history.remember(Turn(Speaker.CALLER, text))
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=True, is_final=True))
            case AgentSaid(text=text) if not self._muted:
                # Not speech to discard: what the agent began to say was said.
                self._last_reply = Turn(Speaker.ASSISTANT, text)
                self._context.history.remember(self._last_reply)
                self._outbox.put(TranscriptProduced(text, speaker_is_caller=False, is_final=True))
            case AgentCorrected():
                self._correct(signal)
            case ToolRequested(tool_call_id=tool_call_id, expects_response=True):
                await self._send(protocol.refuse_tool(tool_call_id))
            case ServiceError():
                # Counted and survived; a refusal the service cannot continue past also drops.
                self._telemetry.stream_error(StreamErrorKind.SERVICE)

    # ------------------------------------------------------------------------------- speech

    async def _play(self, audio: AgentAudio) -> None:
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
                audio_seconds=duration_ms(audio.audio, self._wire_output) / 1000,
            )

    def _correct(self, correction: AgentCorrected) -> None:
        """Remember only what the agent said before the cut; delivered words stay delivered."""
        reply = self._last_reply
        if reply is None or reply.text != correction.original:
            return
        said = Turn(Speaker.ASSISTANT, correction.said) if correction.said.strip() else None
        self._context.history.replace(reply, said)
        self._last_reply = said

    def _discard_speech(self) -> None:
        self._outbox.discard_speech()
        self._outbound.reset()
