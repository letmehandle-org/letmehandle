"""A speech provider for any service speaking the OpenAI Realtime-compatible protocol.

What the service can do is told to this class, not written into it. A compatible server decides
its own languages and voices, and a list of one vendor's facts in here would be a lie about the
next server somebody points it at (D-008).

The capabilities it declares about itself are the ones this adapter implements rather than the
ones the protocol allows: barge-in, because the session acts on the service's speech-started
signal; context updates, because instructions can be sent again mid-session; reconnection,
because a dropped connection is replaced and told what it missed.

It does not greet. A session opens listening and the model first speaks in answer to the caller, so
the greeting a connection is given goes unsaid on this protocol; the caller speaks first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.realtime.context import SessionContext
from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT
from letmehandle.adapters.speech.realtime.session import RealtimeSpeechSession, SessionSetup
from letmehandle.adapters.speech.session_support.bounds import (
    DEFAULT_AUDIO_CEILING_SECONDS,
    DEFAULT_HISTORY_TURNS,
)
from letmehandle.adapters.speech.session_support.offer import (
    check_session_request,
    checked_capabilities,
)
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
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

PROVIDER_NAME: Final = "realtime"


class RealtimeSpeechProvider(SpeechProvider):
    """Opens realtime speech sessions over connections it is handed a way to open."""

    def __init__(
        self,
        opener: ConnectionOpener,
        metrics: MetricsRecorder,
        *,
        languages: Sequence[str],
        input_formats: Sequence[AudioFormat],
        output_format: AudioFormat,
        transcription_model: str | None = None,
        reconnect: ReconnectPolicy | None = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        audio_ceiling_seconds: float = DEFAULT_AUDIO_CEILING_SECONDS,
        timekeeping: Timekeeping | None = None,
    ) -> None:
        if audio_ceiling_seconds <= 0:
            raise InvariantError("a session must be able to hold some audio")
        self._opener = opener
        self._metrics = metrics
        self._capabilities = checked_capabilities(
            wire_format=WIRE_FORMAT,
            languages=languages,
            input_formats=input_formats,
            output_format=output_format,
            barge_in=True,
            context_updates_mid_session=True,
            reconnection=True,
        )
        self._output_format = output_format
        self._transcription_model = transcription_model
        self._reconnect = reconnect or ReconnectPolicy()
        self._history_turns = history_turns
        self._audio_ceiling_seconds = audio_ceiling_seconds
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
        session = RealtimeSpeechSession(
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
                instructions=system_context,
                voice_id=voice_id,
                language=locale.split("-")[0],
                transcription_model=self._transcription_model,
                history_turns=self._history_turns,
            ),
            SessionTelemetry(self._metrics, self._timekeeping.clock, self.name),
        )
        await session.start()
        return session
