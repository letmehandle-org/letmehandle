"""The speech contract, run against the GPT-Live adapter and a service speaking its protocol."""

from __future__ import annotations

import asyncio

import pytest

from letmehandle.adapters.speech.gpt_live.provider import GptLiveSpeechProvider
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)
from tests.contracts.speech import SpeechProviderContract
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_gpt_live_connection import ScriptedGptLiveService


class TestGptLiveSpeechProvider(SpeechProviderContract):
    @pytest.fixture
    def provider(self) -> GptLiveSpeechProvider:
        self.service = ScriptedGptLiveService()
        return GptLiveSpeechProvider(
            self.service.open,
            RecordingMetrics(),
            model="a-live-model",
            languages=("en", "en-GB"),
            input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND),
            output_format=SPEECH_WIDEBAND,
        )

    async def _assert_nothing_queued(self, session: object) -> None:
        """No assistant audio reaches the consumer ahead of the caller's next words."""
        assert isinstance(session, SpeechSession)
        self.service.current.caller_said("marker")
        events = session.events()
        async with asyncio.timeout(2):
            first = await anext(events)
            second = await anext(events)
        assert not isinstance(first, AudioProduced), "interruption must discard queued audio"
        assert first == SpeechStarted(by_caller=True)
        assert second == TranscriptProduced("marker", speaker_is_caller=True, is_final=False)
