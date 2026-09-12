"""The voice-provider contract, run against the shipped catalogue."""

from __future__ import annotations

import pytest

from letmehandle.adapters.voice.builtin import (
    SHIPPED_DEFAULT_VOICE_ID,
    SHIPPED_VOICES,
    BuiltInVoiceProvider,
    built_in_voice_provider,
)
from letmehandle.domain.errors import CapabilityNotSupportedError
from tests.contracts.other_ports import VoiceProviderContract

SAMPLE = b"a short recording of this voice"


class TestBuiltInVoiceProvider(VoiceProviderContract):
    @pytest.fixture
    def voices(self) -> BuiltInVoiceProvider:
        return built_in_voice_provider()

    async def test_without_samples_it_declares_no_preview_and_refuses_to_serve_one(
        self, voices: BuiltInVoiceProvider
    ) -> None:
        # The pair that has to stay true together: a declaration of false and a call that
        # raises. Either one alone lets a preview control reach a caller and play silence.
        assert not voices.capabilities.preview
        with pytest.raises(CapabilityNotSupportedError):
            await voices.sample_audio(voices.default_voice_id)

    async def test_with_samples_it_declares_preview_and_serves_the_audio(self) -> None:
        provider = BuiltInVoiceProvider(
            SHIPPED_VOICES,
            default_voice_id=SHIPPED_DEFAULT_VOICE_ID,
            samples={SHIPPED_DEFAULT_VOICE_ID: SAMPLE},
        )
        assert provider.capabilities.preview
        assert await provider.sample_audio(SHIPPED_DEFAULT_VOICE_ID) == SAMPLE
