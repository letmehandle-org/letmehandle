"""A speech provider for OpenAI GPT-Live (D-008: a different protocol is a second adapter).

What the service can do is told to this class, not written into it: its languages are the
deployment's `SPEECH_LANGUAGES`, and its voices the deployment's catalogue.

The capabilities it declares are the ones this adapter implements, with the protocol's limits
stated rather than smoothed over:

- barge-in, because the model listens while it speaks and stops when the caller talks over it. The
  service does it; nothing is sent (see `session`).
- context updates mid-session, in the protocol's own sense: instructions cannot be replaced once a
  session has started, so what changed is added to them.
- reconnection, in the only sense the protocol allows. A session cannot be resumed, so a replacement
  is a new session started with the instructions as they now stand and a bounded record of what was
  said, and told not to greet the caller again.

It greets. Once a session has started it is told, in the opening language, to say the greeting
without waiting for the caller and then listen (D-039).

The session speaks the format its caller audio arrives in, where the protocol can carry it, so a
phone line's G.711 audio passes through unconverted; the assistant's audio is produced in
`output_format`, converted from that wire format when they differ.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.gpt_live.context import SessionContext
from letmehandle.adapters.speech.gpt_live.language import base_language
from letmehandle.adapters.speech.gpt_live.protocol import DEFAULT_WIRE_FORMAT, wire_format_for
from letmehandle.adapters.speech.gpt_live.session import GptLiveSpeechSession, LiveOptions
from letmehandle.adapters.speech.gpt_live.turns import DEFAULT_GAP_MS
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

PROVIDER_NAME: Final = "gpt_live"

# How long closing waits for the service to finalise a session before letting it go anyway.
DEFAULT_CLOSE_TIMEOUT_SECONDS: Final = 5.0


class GptLiveSpeechProvider(SpeechProvider):
    """Opens GPT-Live sessions with one model, over connections it is handed a way to open."""

    def __init__(
        self,
        opener: ConnectionOpener,
        metrics: MetricsRecorder,
        *,
        model: str,
        languages: Sequence[str],
        input_formats: Sequence[AudioFormat],
        output_format: AudioFormat,
        reconnect: ReconnectPolicy | None = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        audio_ceiling_seconds: float = DEFAULT_AUDIO_CEILING_SECONDS,
        start_timeout: float = DEFAULT_OPEN_TIMEOUT_SECONDS,
        close_timeout: float = DEFAULT_CLOSE_TIMEOUT_SECONDS,
        turn_gap_ms: int = DEFAULT_GAP_MS,
        timekeeping: Timekeeping | None = None,
    ) -> None:
        if not model.strip():
            raise InvariantError("a GPT-Live session needs a model to run")
        if audio_ceiling_seconds <= 0:
            raise InvariantError("a session must be able to hold some audio")
        if start_timeout <= 0 or close_timeout <= 0:
            raise InvariantError("a session must be given some time to start and to finish")
        if turn_gap_ms <= 0:
            raise InvariantError("a turn must be allowed some quiet before it settles")
        self._opener = opener
        self._metrics = metrics
        self._model = model
        # Checked against the protocol's default format. Every format it can carry is linear or
        # G.711 audio, which converts to and from the same things, so the format a session chooses
        # changes no answer here.
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
        self._reconnect = reconnect or ReconnectPolicy()
        self._history_turns = history_turns
        self._audio_ceiling_seconds = audio_ceiling_seconds
        self._start_timeout = start_timeout
        self._close_timeout = close_timeout
        self._turn_gap_ms = turn_gap_ms
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
        wire_format = wire_format_for(input_format)
        session = GptLiveSpeechSession(
            SessionSetup(
                provider=self.name,
                opener=self._opener,
                input_format=input_format,
                output_format=self._output_format,
                reconnect=self._reconnect,
                audio_ceiling_seconds=self._audio_ceiling_seconds,
                timekeeping=self._timekeeping,
            ),
            SessionContext(
                model=self._model,
                instructions=system_context,
                voice_id=voice_id,
                wire_format=wire_format,
                greeting=greeting,
                language=base_language(locale),
                history_turns=self._history_turns,
            ),
            SessionTelemetry(self._metrics, self._timekeeping.clock, self.name),
            LiveOptions(
                wire_format=wire_format,
                languages=self._capabilities.languages,
                start_timeout=self._start_timeout,
                close_timeout=self._close_timeout,
                turn_gap_ms=self._turn_gap_ms,
                metrics=self._metrics,
            ),
        )
        await session.start()
        return session
