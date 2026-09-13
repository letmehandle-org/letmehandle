"""The speech contract, run against the ElevenLabs adapter and an agent speaking its protocol."""

from __future__ import annotations

import asyncio

import pytest

from letmehandle.adapters.speech.elevenlabs.provider import ElevenLabsSpeechProvider
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from letmehandle.domain.ports.speech import SpeechSession, TranscriptProduced
from tests.contracts.speech import SpeechProviderContract
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_elevenlabs_connection import ScriptedElevenLabsService


class TestElevenLabsSpeechProvider(SpeechProviderContract):
    @pytest.fixture
    def provider(self) -> ElevenLabsSpeechProvider:
        self.service = ScriptedElevenLabsService()
        return ElevenLabsSpeechProvider(
            self.service.open,
            RecordingMetrics(),
            languages=("en", "en-GB"),
            input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND),
            output_format=SPEECH_WIDEBAND,
        )

    async def _assert_nothing_queued(self, session: object) -> None:
        """Nothing the agent was saying reaches the consumer ahead of the caller's next turn."""
        assert isinstance(session, SpeechSession)
        self.service.current.caller_said("marker")
        events = session.events()
        async with asyncio.timeout(2):
            first = await anext(events)
        assert first == TranscriptProduced("marker", speaker_is_caller=True, is_final=True)
