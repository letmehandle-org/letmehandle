"""A realtime provider wired to the scripted service, with time under the test's control."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

import pytest

from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT
from letmehandle.adapters.speech.realtime.provider import RealtimeSpeechProvider
from letmehandle.adapters.speech.realtime.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.realtime.timing import Timekeeping
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_realtime_connection import ScriptedRealtimeService

if TYPE_CHECKING:
    from letmehandle.domain.models.audio import AudioFormat


class ManualClock:
    """A monotonic clock that moves only when told to."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class RecordedSleep:
    """Records every wait and returns at once, unless a test holds it."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.hold: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.entered.set()
        if self.hold is not None:
            await self.hold.wait()


class ProviderFactory(Protocol):
    def __call__(
        self,
        *,
        output_format: AudioFormat = ...,
        queue_size: int = ...,
        history_turns: int = ...,
        max_attempts: int = ...,
        transcription_model: str | None = ...,
    ) -> RealtimeSpeechProvider: ...


@pytest.fixture
def service() -> ScriptedRealtimeService:
    return ScriptedRealtimeService()


@pytest.fixture
def metrics() -> RecordingMetrics:
    return RecordingMetrics()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def sleep() -> RecordedSleep:
    return RecordedSleep()


@pytest.fixture
def make_provider(
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    clock: ManualClock,
    sleep: RecordedSleep,
) -> ProviderFactory:
    def build(
        *,
        output_format: AudioFormat = SPEECH_WIDEBAND,
        queue_size: int = 64,
        history_turns: int = 8,
        max_attempts: int = 3,
        transcription_model: str | None = None,
    ) -> RealtimeSpeechProvider:
        return RealtimeSpeechProvider(
            service.open,
            metrics,
            languages=("en",),
            input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND, WIRE_FORMAT),
            output_format=output_format,
            transcription_model=transcription_model,
            reconnect=ReconnectPolicy(max_attempts, 0.5, 2.0),
            history_turns=history_turns,
            queue_size=queue_size,
            timekeeping=Timekeeping(clock=clock, sleep=sleep, draw=lambda: 0.5),
        )

    return build


@pytest.fixture
def provider(make_provider: ProviderFactory) -> RealtimeSpeechProvider:
    return make_provider()
