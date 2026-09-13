"""A speech provider for OpenAI GPT-Live, a third protocol behind the same port (D-040)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.gpt_live.context import SessionContext
from letmehandle.adapters.speech.gpt_live.protocol import DEFAULT_WIRE_FORMAT, wire_format_for
from letmehandle.adapters.speech.gpt_live.session import GptLiveSpeechSession, LiveOptions
from letmehandle.adapters.speech.gpt_live.turns import DEFAULT_GAP_MS
from letmehandle.adapters.speech.session_support.bounds import (
    DEFAULT_AUDIO_CEILING_SECONDS,
    DEFAULT_HISTORY_TURNS,
    DEFAULT_OPEN_TIMEOUT_SECONDS,
)
from letmehandle.adapters.speech.session_support.offer import checked_capabilities
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

PROVIDER_NAME: Final = "gpt_live"

# How long closing waits for the service to finalise a session before letting it go anyway.
DEFAULT_CLOSE_TIMEOUT_SECONDS: Final = 5.0


class GptLiveSpeechProvider(StreamingSpeechProvider):
    """Opens GPT-Live sessions with one model that greet first and speak their caller's format."""

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
        super().__init__(
            opener,
            metrics,
            name=PROVIDER_NAME,
            # Checked against the default format; every format the protocol carries converts alike.
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
        if start_timeout <= 0 or close_timeout <= 0:
            raise InvariantError("a session must be given some time to start and to finish")
        if turn_gap_ms <= 0:
            raise InvariantError("a turn must be allowed some quiet before it settles")
        self._model = model
        self._history_turns = history_turns
        self._start_timeout = start_timeout
        self._close_timeout = close_timeout
        self._turn_gap_ms = turn_gap_ms

    def _session(
        self, setup: SessionSetup, telemetry: SessionTelemetry, request: SessionRequest
    ) -> GptLiveSpeechSession:
        wire_format = wire_format_for(request.input_format)
        context = SessionContext(
            model=self._model,
            instructions=request.instructions,
            voice_id=request.voice_id,
            wire_format=wire_format,
            greeting=request.greeting,
            language=request.language,
            history_turns=self._history_turns,
        )
        options = LiveOptions(
            wire_format=wire_format,
            languages=self._capabilities.languages,
            start_timeout=self._start_timeout,
            close_timeout=self._close_timeout,
            turn_gap_ms=self._turn_gap_ms,
        )
        return GptLiveSpeechSession(setup, context, telemetry, options)
