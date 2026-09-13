"""What every speech provider shares: checking a session request, then starting its session."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.session_support.offer import base_language, check_session_request
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.session_support.streaming import SessionSetup
from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
from letmehandle.adapters.speech.session_support.timing import Timekeeping
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.speech import SpeechProvider

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.streaming import StreamingSpeechSession
    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener
    from letmehandle.domain.models.audio import AudioFormat
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechCapabilities, SpeechSession


@dataclass(frozen=True, slots=True)
class SessionRequest:
    """What the caller of `connect` asked a session for."""

    instructions: str
    voice_id: str
    greeting: str
    locale: str
    input_format: AudioFormat

    @property
    def language(self) -> str:
        return base_language(self.locale)


class StreamingSpeechProvider(SpeechProvider):
    """A `SpeechProvider` opening sessions over connections from one opener."""

    def __init__(
        self,
        opener: ConnectionOpener,
        metrics: MetricsRecorder,
        *,
        name: str,
        capabilities: SpeechCapabilities,
        output_format: AudioFormat,
        reconnect: ReconnectPolicy | None,
        audio_ceiling_seconds: float,
        timekeeping: Timekeeping | None,
    ) -> None:
        if audio_ceiling_seconds <= 0:
            raise InvariantError("a session must be able to hold some audio")
        self._opener = opener
        self._metrics = metrics
        self._name = name
        self._capabilities = capabilities
        self._output_format = output_format
        self._reconnect = reconnect or ReconnectPolicy()
        self._audio_ceiling_seconds = audio_ceiling_seconds
        self._timekeeping = timekeeping or Timekeeping()

    @property
    def name(self) -> str:
        return self._name

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
        setup = SessionSetup(
            provider=self.name,
            opener=self._opener,
            input_format=input_format,
            output_format=self._output_format,
            reconnect=self._reconnect,
            audio_ceiling_seconds=self._audio_ceiling_seconds,
            timekeeping=self._timekeeping,
        )
        request = SessionRequest(system_context, voice_id, greeting, locale, input_format)
        telemetry = SessionTelemetry(self._metrics, self._timekeeping.clock, self.name)
        session = self._session(setup, telemetry, request)
        await session.start()
        return session

    @abstractmethod
    def _session(
        self, setup: SessionSetup, telemetry: SessionTelemetry, request: SessionRequest
    ) -> StreamingSpeechSession[Any]:
        """A session for `request`, not yet started."""
