"""The voice-provider contract, run against the configured catalogue.

Twice, in both of the configurations this provider runs in: with sample audio and without.
The contract asserts that a provider's declared capabilities match what it does, and that is
the one assertion a single configuration cannot make on its own.
"""

from __future__ import annotations

import pytest

from letmehandle.adapters.voice.builtin import BuiltInVoiceProvider
from letmehandle.bootstrap import build_voice_provider
from letmehandle.domain.ports.voice import VoiceProvider, VoiceSample
from tests.contracts.other_ports import VoiceProviderContract
from tests.support.config import EXAMPLE_DEFAULT_VOICE, EXAMPLE_VOICES, make_settings

SAMPLE = VoiceSample(audio=b"a short recording of this voice", media_type="audio/mpeg")


class TestBuiltInVoiceProvider(VoiceProviderContract):
    """What the application actually builds: a catalogue with nothing to play yet."""

    @pytest.fixture
    def voices(self) -> VoiceProvider:
        return build_voice_provider(make_settings())


class TestBuiltInVoiceProviderWithSamples(VoiceProviderContract):
    """The same provider given sample audio, which is what turns preview on."""

    @pytest.fixture
    def voices(self) -> BuiltInVoiceProvider:
        return BuiltInVoiceProvider(
            EXAMPLE_VOICES,
            default_voice_id=EXAMPLE_DEFAULT_VOICE,
            samples={EXAMPLE_DEFAULT_VOICE: SAMPLE},
        )

    async def test_it_serves_the_audio_it_was_given(self, voices: BuiltInVoiceProvider) -> None:
        assert await voices.preview(EXAMPLE_DEFAULT_VOICE) == SAMPLE
