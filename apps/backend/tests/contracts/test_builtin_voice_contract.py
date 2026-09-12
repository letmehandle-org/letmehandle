"""The voice-provider contract, run against the shipped catalogue.

Twice, in both of the configurations this provider ships in: with sample audio and without.
The contract asserts that a provider's declared capabilities match what it does, and that is
the one assertion a single configuration cannot make on its own.
"""

from __future__ import annotations

import pytest

from letmehandle.adapters.voice.builtin import (
    SHIPPED_DEFAULT_VOICE_ID,
    SHIPPED_VOICES,
    BuiltInVoiceProvider,
    built_in_voice_provider,
)
from letmehandle.domain.ports.voice import VoiceSample
from tests.contracts.other_ports import VoiceProviderContract

SAMPLE = VoiceSample(audio=b"a short recording of this voice", media_type="audio/mpeg")


class TestBuiltInVoiceProvider(VoiceProviderContract):
    """What the application actually builds: a catalogue with nothing to play yet."""

    @pytest.fixture
    def voices(self) -> BuiltInVoiceProvider:
        return built_in_voice_provider()


class TestBuiltInVoiceProviderWithSamples(VoiceProviderContract):
    """The same provider given sample audio, which is what turns preview on."""

    @pytest.fixture
    def voices(self) -> BuiltInVoiceProvider:
        return BuiltInVoiceProvider(
            SHIPPED_VOICES,
            default_voice_id=SHIPPED_DEFAULT_VOICE_ID,
            samples={SHIPPED_DEFAULT_VOICE_ID: SAMPLE},
        )

    async def test_it_serves_the_audio_it_was_given(self, voices: BuiltInVoiceProvider) -> None:
        assert await voices.preview(SHIPPED_DEFAULT_VOICE_ID) == SAMPLE
