"""A speech provider for ElevenLabs Agents, a second protocol behind the port (D-008, D-039)."""

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
from letmehandle.adapters.speech.session_support.offer import base_language, checked_capabilities
from letmehandle.adapters.speech.session_support.provider import StreamingSpeechProvider
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.adapters.speech.session_support.provider import SessionRequest
    from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
    from letmehandle.adapters.speech.session_support.streaming import SessionSetup
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener
    from letmehandle.domain.models.audio import AudioFormat
    from letmehandle.domain.ports.metrics import MetricsRecorder

PROVIDER_NAME: Final = "elevenlabs"


class ElevenLabsSpeechProvider(StreamingSpeechProvider):
    """Opens conversations with one agent; an agent of several languages is sent no voice."""

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
        super().__init__(
            opener,
            metrics,
            name=PROVIDER_NAME,
            # Checked against the default formats; every format the protocol names converts alike.
            capabilities=checked_capabilities(
                wire_format=DEFAULT_WIRE_FORMAT,
                languages=languages,
                input_formats=input_formats,
                output_format=output_format,
                barge_in=True,
                context_updates_mid_session=True,
                reconnection=True,
            ),
            output_format=output_format,
            reconnect=reconnect,
            audio_ceiling_seconds=audio_ceiling_seconds,
            timekeeping=timekeeping,
        )
        if initiation_timeout <= 0:
            raise InvariantError("a conversation must be given some time to begin")
        spoken = tuple(dict.fromkeys(base_language(language) for language in languages))
        self._switching = switching(spoken) if len(spoken) > 1 else None
        self._history_turns = history_turns
        self._initiation_timeout = initiation_timeout

    def _session(
        self, setup: SessionSetup, telemetry: SessionTelemetry, request: SessionRequest
    ) -> ElevenLabsSpeechSession:
        context = ConversationContext(
            instructions=request.instructions,
            voice_id=request.voice_id if self._switching is None else None,
            greeting=request.greeting,
            language=request.language,
            switching=self._switching,
            history_turns=self._history_turns,
        )
        return ElevenLabsSpeechSession(
            setup, context, telemetry, initiation_timeout=self._initiation_timeout
        )
