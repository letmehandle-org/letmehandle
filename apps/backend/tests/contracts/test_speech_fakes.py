"""The speech contract, run against the in-memory session."""

from __future__ import annotations

import pytest

from tests.contracts.fakes import EchoSpeechProvider
from tests.contracts.speech import SpeechProviderContract


class TestEchoSpeechProvider(SpeechProviderContract):
    @pytest.fixture
    def provider(self) -> EchoSpeechProvider:
        return EchoSpeechProvider()

    async def test_connecting_passes_through_what_the_caller_asked_for(
        self, provider: EchoSpeechProvider
    ) -> None:
        # The input format is the caller's, not the provider's assumption: the audio comes from
        # a transport whose format this provider does not choose.
        session = await provider.connect(
            system_context="you are answering for someone",
            voice_id="calm",
            greeting="Hello.",
            locale="en-GB",
            input_format=provider.capabilities.input_formats[0],
        )
        await session.close()

        assert provider.connections[0]["voice_id"] == "calm"
        assert provider.connections[0]["locale"] == "en-GB"

    async def test_a_closed_session_refuses_more_audio(self, provider: EchoSpeechProvider) -> None:
        session = await provider.connect(
            system_context="c",
            voice_id="calm",
            greeting="Hello.",
            locale="en",
            input_format=provider.capabilities.input_formats[0],
        )
        await session.close()
        assert getattr(session, "is_closed", False)
