"""A speech provider for ElevenLabs Agents (D-008: a different protocol is a second adapter).

What the agent can do is told to this class, not written into it. An agent's languages and voices
are its configuration at the service, and a list of them in here would be a lie about the next
agent somebody points it at.

The capabilities it declares are the ones this adapter implements, with the protocol's limits
stated rather than smoothed over:

- barge-in, because the service interrupts its own agent when the caller speaks over it and says
  so, and the session discards what was queued when it does;
- context updates mid-session, in the protocol's own weaker sense: a non-interrupting update the
  agent takes into account, added to the conversation rather than replacing its instructions;
- reconnection, in the only sense the protocol allows. A dropped conversation cannot be resumed,
  so a replacement is a new conversation opened with the instructions, the updates and a bounded
  record of what was said written into its prompt, and asked not to greet the caller again. The
  model is reminded of the conversation; it does not get it back.

An agent told it speaks more than one language is sent no voice (D-039). Its voice for each
language is its own configuration — its base voice, and a language preset for every other
language — and the protocol holds a voice sent by the client for the whole conversation, so a
caller whose language the agent switched to would be answered in the voice of the one it left.
Such an agent is also told, after its instructions, to change language with the service's tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.elevenlabs.context import ConversationContext, switching
from letmehandle.adapters.speech.elevenlabs.protocol import DEFAULT_WIRE_FORMAT
from letmehandle.adapters.speech.elevenlabs.session import ElevenLabsSpeechSession
from letmehandle.adapters.speech.session_support.bounds import (
    DEFAULT_AUDIO_CEILING_SECONDS,
    DEFAULT_HISTORY_TURNS,
    DEFAULT_OPEN_TIMEOUT_SECONDS,
)
from letmehandle.adapters.speech.session_support.offer import (
    check_session_request,
    checked_capabilities,
)
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.session_support.streaming import SessionSetup
from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
from letmehandle.adapters.speech.session_support.timing import Timekeeping
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.speech import SpeechProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener
    from letmehandle.domain.models.audio import AudioFormat
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechCapabilities, SpeechSession

PROVIDER_NAME: Final = "elevenlabs"


class ElevenLabsSpeechProvider(SpeechProvider):
    """Opens conversations with one ElevenLabs agent over connections it is handed a way to open."""

    def __init__(
        self,
        opener: ConnectionOpener,
        metrics: MetricsRecorder,
        *,
        languages: Sequence[str],
        input_formats: Sequence[AudioFormat],
        output_format: AudioFormat,
        reconnect: ReconnectPolicy | None = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        audio_ceiling_seconds: float = DEFAULT_AUDIO_CEILING_SECONDS,
        initiation_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
        timekeeping: Timekeeping | None = None,
    ) -> None:
        if audio_ceiling_seconds <= 0:
            raise InvariantError("a session must be able to hold some audio")
        if initiation_timeout <= 0:
            raise InvariantError("a conversation must be given some time to begin")
        self._opener = opener
        self._metrics = metrics
        # Checked against the agent's default formats. Every format the protocol can name is
        # linear or μ-law audio, which converts to and from the same things, so an agent
        # configured otherwise changes no answer here.
        self._capabilities = checked_capabilities(
            wire_format=DEFAULT_WIRE_FORMAT,
            languages=languages,
            input_formats=input_formats,
            output_format=output_format,
            barge_in=True,
            context_updates_mid_session=True,
            reconnection=True,
        )
        self._output_format = output_format
        spoken = tuple(dict.fromkeys(language.split("-")[0] for language in languages))
        self._switching = switching(spoken) if len(spoken) > 1 else None
        self._reconnect = reconnect or ReconnectPolicy()
        self._history_turns = history_turns
        self._audio_ceiling_seconds = audio_ceiling_seconds
        self._initiation_timeout = initiation_timeout
        self._timekeeping = timekeeping or Timekeeping()

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def capabilities(self) -> SpeechCapabilities:
        return self._capabilities

    async def connect(
        self,
        *,
        system_context: str,
        voice_id: str,
        greeting: str,
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        check_session_request(
            self.name,
            self._capabilities,
            locale=locale,
            input_format=input_format,
            voice_id=voice_id,
        )
        session = ElevenLabsSpeechSession(
            SessionSetup(
                provider=self.name,
                opener=self._opener,
                input_format=input_format,
                output_format=self._output_format,
                reconnect=self._reconnect,
                audio_ceiling_seconds=self._audio_ceiling_seconds,
                timekeeping=self._timekeeping,
            ),
            ConversationContext(
                instructions=system_context,
                voice_id=voice_id if self._switching is None else None,
                greeting=greeting,
                language=locale.split("-")[0],
                switching=self._switching,
                history_turns=self._history_turns,
            ),
            SessionTelemetry(self._metrics, self._timekeeping.clock, self.name),
            initiation_timeout=self._initiation_timeout,
        )
        await session.start()
        return session
