"""A speech provider for any service speaking the OpenAI Realtime-compatible protocol (D-008)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.realtime.context import SessionContext
from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT
from letmehandle.adapters.speech.realtime.session import RealtimeSpeechSession
from letmehandle.adapters.speech.session_support.bounds import (
    DEFAULT_AUDIO_CEILING_SECONDS,
    DEFAULT_HISTORY_TURNS,
)
from letmehandle.adapters.speech.session_support.offer import checked_capabilities
from letmehandle.adapters.speech.session_support.provider import StreamingSpeechProvider

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

PROVIDER_NAME: Final = "realtime"


class RealtimeSpeechProvider(StreamingSpeechProvider):
    """Opens realtime sessions that listen first, with the languages and voices it is told."""

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
        super().__init__(
            opener,
            metrics,
            name=PROVIDER_NAME,
            capabilities=checked_capabilities(
                wire_format=WIRE_FORMAT,
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
        self._transcription_model = transcription_model
        self._history_turns = history_turns

    def _session(
        self, setup: SessionSetup, telemetry: SessionTelemetry, request: SessionRequest
    ) -> RealtimeSpeechSession:
        context = SessionContext(
            instructions=request.instructions,
            voice_id=request.voice_id,
            language=request.language,
            transcription_model=self._transcription_model,
            history_turns=self._history_turns,
        )
        return RealtimeSpeechSession(setup, context, telemetry)
