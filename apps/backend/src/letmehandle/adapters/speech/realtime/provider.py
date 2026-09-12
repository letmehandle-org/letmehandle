"""A speech provider for any service speaking the OpenAI Realtime-compatible protocol.

What the service can do is told to this class, not written into it. A compatible server decides
its own languages and voices, and a list of one vendor's facts in here would be a lie about the
next server somebody points it at (D-008).

The capabilities it declares about itself are the ones this adapter implements rather than the
ones the protocol allows: barge-in, because the session acts on the service's speech-started
signal; context updates, because instructions can be sent again mid-session; reconnection,
because a dropped connection is replaced and told what it missed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.audio.conversion import can_convert
from letmehandle.adapters.speech.realtime.context import SessionContext
from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT
from letmehandle.adapters.speech.realtime.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.realtime.session import RealtimeSpeechSession, SessionSetup
from letmehandle.adapters.speech.realtime.telemetry import SessionTelemetry
from letmehandle.adapters.speech.realtime.timing import Timekeeping
from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError
from letmehandle.domain.ports.speech import SpeechCapabilities, SpeechProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.adapters.speech.realtime.connection import ConnectionOpener
    from letmehandle.domain.models.audio import AudioFormat
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechSession

PROVIDER_NAME: Final = "realtime"

# Enough for several seconds of model speech at the sizes services send it in, and small enough
# that a stalled consumer is a stalled reader within a few seconds rather than a growing heap.
DEFAULT_QUEUE_SIZE: Final = 256

# Enough to carry a conversation across a reconnect; a long call's opening minutes matter less
# to its next sentence than the cost of replaying them.
DEFAULT_HISTORY_TURNS: Final = 24


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
        queue_size: int = DEFAULT_QUEUE_SIZE,
        timekeeping: Timekeeping | None = None,
    ) -> None:
        if not languages:
            raise InvariantError("a speech provider that speaks no language can serve nobody")
        if not input_formats:
            raise InvariantError("a speech provider must accept audio in at least one format")
        refused = [str(each) for each in input_formats if not can_convert(each, WIRE_FORMAT)]
        if refused:
            # Refused here rather than at the first connect, so a misconfiguration stops the
            # application starting instead of failing the first call that uses the format.
            raise InvariantError(f"audio in these formats cannot be converted: {refused}")
        if not can_convert(WIRE_FORMAT, output_format):
            raise InvariantError(f"audio cannot be produced in {output_format}")
        if queue_size < 1:
            raise InvariantError("an event queue must hold at least one event")
        self._opener = opener
        self._metrics = metrics
        self._capabilities = SpeechCapabilities(
            barge_in=True,
            context_updates_mid_session=True,
            reconnection=True,
            languages=tuple(languages),
            input_formats=tuple(input_formats),
            output_format=output_format,
        )
        self._output_format = output_format
        self._transcription_model = transcription_model
        self._reconnect = reconnect or ReconnectPolicy()
        self._history_turns = history_turns
        self._queue_size = queue_size
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
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        if not self._capabilities.speaks(locale):
            raise CapabilityNotSupportedError(self.name, f"speaking {locale}")
        if input_format not in self._capabilities.input_formats:
            raise CapabilityNotSupportedError(self.name, f"audio input in {input_format}")
        if not voice_id.strip():
            raise InvariantError("a speech session needs a voice to speak in")
        session = RealtimeSpeechSession(
            SessionSetup(
                provider=self.name,
                opener=self._opener,
                input_format=input_format,
                output_format=self._output_format,
                reconnect=self._reconnect,
                queue_size=self._queue_size,
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
