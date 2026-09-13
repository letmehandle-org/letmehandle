"""A GPT-Live provider wired to the scripted service, with time under the test's control."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from letmehandle.adapters.speech.gpt_live.provider import GptLiveSpeechProvider
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.session_support.timing import Timekeeping
from letmehandle.domain.models.audio import (
    SPEECH_WIDEBAND,
    TELEPHONY_NARROWBAND,
    AudioEncoding,
    AudioFormat,
)
from tests.support.scripted_gpt_live_connection import ScriptedGptLiveService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tests.support.recording_metrics import RecordingMetrics
    from tests.unit.adapters.speech.conftest import ManualClock, RecordedSleep

TELEPHONY_ALAW = AudioFormat(AudioEncoding.ALAW, 8_000)
PCM_24K = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)
PCM_48K = AudioFormat(AudioEncoding.PCM_S16LE, 48_000)


class ProviderFactory(Protocol):
    def __call__(
        self,
        *,
        languages: Sequence[str] = ...,
        output_format: AudioFormat = ...,
        audio_ceiling_seconds: float = ...,
        history_turns: int = ...,
        max_attempts: int = ...,
        start_timeout: float = ...,
        close_timeout: float = ...,
    ) -> GptLiveSpeechProvider: ...


@pytest.fixture
def service() -> ScriptedGptLiveService:
    return ScriptedGptLiveService()


@pytest.fixture
def make_provider(
    service: ScriptedGptLiveService,
    metrics: RecordingMetrics,
    clock: ManualClock,
    sleep: RecordedSleep,
) -> ProviderFactory:
    def build(
        *,
        languages: Sequence[str] = ("en",),
        output_format: AudioFormat = TELEPHONY_NARROWBAND,
        audio_ceiling_seconds: float = 60.0,
        history_turns: int = 8,
        max_attempts: int = 3,
        start_timeout: float = 1.0,
        close_timeout: float = 1.0,
    ) -> GptLiveSpeechProvider:
        return GptLiveSpeechProvider(
            service.open,
            metrics,
            model="a-live-model",
            languages=languages,
            input_formats=(TELEPHONY_NARROWBAND, SPEECH_WIDEBAND, PCM_24K, TELEPHONY_ALAW, PCM_48K),
            output_format=output_format,
            reconnect=ReconnectPolicy(max_attempts, 0.5, 2.0),
            history_turns=history_turns,
            audio_ceiling_seconds=audio_ceiling_seconds,
            start_timeout=start_timeout,
            close_timeout=close_timeout,
            timekeeping=Timekeeping(clock=clock, sleep=sleep, draw=lambda: 0.5),
        )

    return build


@pytest.fixture
def provider(make_provider: ProviderFactory) -> GptLiveSpeechProvider:
    return make_provider()
