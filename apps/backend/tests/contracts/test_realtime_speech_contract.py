"""The speech contract, run against the realtime adapter and a service speaking its protocol."""

from __future__ import annotations

import asyncio

import pytest

from letmehandle.adapters.speech.realtime.provider import RealtimeSpeechProvider
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from letmehandle.domain.ports.speech import AudioProduced, SpeechSession, TranscriptProduced
from tests.contracts.speech import SpeechProviderContract
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_realtime_connection import ScriptedRealtimeService


class TestRealtimeSpeechProvider(SpeechProviderContract):
    @pytest.fixture
    def provider(self) -> RealtimeSpeechProvider:
        self.service = ScriptedRealtimeService()
        return RealtimeSpeechProvider(
            self.service.open,
            RecordingMetrics(),
            languages=("en", "en-GB"),
            input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND),
            output_format=SPEECH_WIDEBAND,
        )

    async def _assert_nothing_queued(self, session: object) -> None:
        """No model audio reaches the consumer once interruption has returned.

        The session exposes no queue to count, so the property is asserted from the outside: the
        service is made to say something new, and the first thing the consumer receives must be
        that, not audio the model produced before it was interrupted.
        """
        assert isinstance(session, SpeechSession)
        self.service.current.caller_said("marker")
        events = session.events()
        async with asyncio.timeout(2):
            first = await anext(events)
        assert not isinstance(first, AudioProduced), "interruption must discard queued audio"
        assert first == TranscriptProduced("marker", speaker_is_caller=True, is_final=False)
