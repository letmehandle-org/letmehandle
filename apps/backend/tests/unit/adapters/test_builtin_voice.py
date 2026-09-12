"""The catalogue provider: what it refuses to be built as, and what it admits it cannot do."""

from __future__ import annotations

import pytest

from letmehandle.adapters.voice.builtin import (
    SHIPPED_DEFAULT_VOICE_ID,
    SHIPPED_VOICES,
    BuiltInVoiceProvider,
    built_in_voice_provider,
)
from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    InvariantError,
    ProviderError,
)
from letmehandle.domain.ports.voice import Voice

CALM = Voice(id="calm", name="Calm", locales=("en",))
BRIGHT = Voice(id="bright", name="Bright", locales=("en", "fr"))
ONLY_FRENCH = Voice(id="claire", name="Claire", locales=("fr",))


def _provider(*, samples: dict[str, bytes] | None = None) -> BuiltInVoiceProvider:
    return BuiltInVoiceProvider(
        (CALM, BRIGHT, ONLY_FRENCH), default_voice_id="calm", samples=samples
    )


def test_it_names_itself() -> None:
    assert _provider().name == "builtin"


def test_it_refuses_an_empty_catalogue() -> None:
    with pytest.raises(InvariantError, match="empty catalogue"):
        BuiltInVoiceProvider((), default_voice_id="calm")


def test_it_refuses_a_default_that_is_not_in_the_catalogue() -> None:
    # The end of the fallback chain. A default nobody can look up turns a call with no
    # expressed preference into a call with no voice.
    with pytest.raises(InvariantError, match="not in the catalogue"):
        BuiltInVoiceProvider((CALM,), default_voice_id="bright")


def test_it_refuses_two_voices_with_the_same_identifier() -> None:
    duplicate = Voice(id="calm", name="Calm, again", locales=("en",))
    with pytest.raises(InvariantError, match="share an identifier"):
        BuiltInVoiceProvider((CALM, duplicate), default_voice_id="calm")


def test_it_refuses_sample_audio_for_a_voice_it_does_not_offer() -> None:
    # Usually a typo in an identifier, which would silently cost the voice it was meant for.
    with pytest.raises(InvariantError, match=r"outside the catalogue: \['ghost'\]"):
        BuiltInVoiceProvider((CALM,), default_voice_id="calm", samples={"ghost": b"audio"})


async def test_it_lists_everything_when_no_language_is_asked_for() -> None:
    assert await _provider().list_voices() == (CALM, BRIGHT, ONLY_FRENCH)


async def test_listing_narrows_to_the_voices_that_speak_the_language() -> None:
    assert await _provider().list_voices("fr") == (BRIGHT, ONLY_FRENCH)


async def test_a_regional_locale_is_served_by_the_language_it_belongs_to() -> None:
    # A catalogue that had to enumerate every regional tag is a catalogue missing one.
    assert await _provider().list_voices("en-GB") == (CALM, BRIGHT)


async def test_a_voice_is_available_exactly_when_it_is_in_the_catalogue() -> None:
    provider = _provider()
    assert await provider.is_available("calm")
    assert not await provider.is_available("cloned")


def test_it_never_claims_to_clone_or_to_run_a_model() -> None:
    # D-009: the mobile interface renders from these. A true here puts a training flow in
    # front of somebody it cannot work for.
    capabilities = _provider(samples={"calm": b"audio"}).capabilities
    assert capabilities.builtin_voices
    assert not capabilities.cloning
    assert not capabilities.custom_voice
    assert not capabilities.local_inference
    assert not capabilities.realtime_streaming


def test_preview_is_declared_only_when_there_is_audio_to_play() -> None:
    assert not _provider().capabilities.preview
    assert _provider(samples={"calm": b"audio"}).capabilities.preview


async def test_it_serves_the_sample_it_was_given() -> None:
    provider = _provider(samples={"calm": b"calm-audio"})
    assert await provider.sample_audio("calm") == b"calm-audio"


async def test_a_provider_with_no_samples_refuses_preview_rather_than_returning_silence() -> None:
    with pytest.raises(CapabilityNotSupportedError) as raised:
        await _provider().sample_audio("calm")
    assert raised.value.capability == "preview"
    assert raised.value.provider == "builtin"


async def test_an_unknown_voice_is_a_domain_error_and_never_a_key_error() -> None:
    provider = _provider(samples={"calm": b"calm-audio"})
    with pytest.raises(ProviderError, match="no voice named 'ghost'") as raised:
        await provider.sample_audio("ghost")
    assert not raised.value.retryable


async def test_a_catalogue_voice_with_no_sample_says_so() -> None:
    # Distinct from an unknown identifier: the voice is real and selectable, and only its
    # preview is missing.
    provider = _provider(samples={"calm": b"calm-audio"})
    with pytest.raises(ProviderError, match="no sample audio for voice 'bright'") as raised:
        await provider.sample_audio("bright")
    assert not raised.value.retryable


async def test_the_shipped_catalogue_speaks_english_and_declares_no_preview() -> None:
    provider = built_in_voice_provider()
    assert provider.default_voice_id == SHIPPED_DEFAULT_VOICE_ID
    assert await provider.list_voices("en") == SHIPPED_VOICES
    # No speech model ships in this phase, so there is nothing to synthesise a preview with.
    assert not provider.capabilities.preview
