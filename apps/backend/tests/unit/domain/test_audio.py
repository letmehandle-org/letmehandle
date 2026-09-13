"""Audio formats and frames, which always say what they are."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.audio import (
    SPEECH_WIDEBAND,
    TELEPHONY_NARROWBAND,
    AudioEncoding,
    AudioFormat,
    AudioFrame,
)


def test_a_format_carries_encoding_rate_and_channels() -> None:
    audio_format = AudioFormat(AudioEncoding.PCM_S16LE, 16_000, channels=2)
    assert audio_format.encoding is AudioEncoding.PCM_S16LE
    assert audio_format.sample_rate_hz == 16_000
    assert audio_format.channels == 2


@pytest.mark.parametrize(("rate", "channels"), [(0, 1), (-1, 1), (16_000, 0), (16_000, -1)])
def test_a_format_that_describes_nothing_is_rejected(rate: int, channels: int) -> None:
    with pytest.raises(InvariantError):
        AudioFormat(AudioEncoding.PCM_S16LE, rate, channels)


def test_the_two_named_formats_are_the_ones_at_the_edges() -> None:
    assert TELEPHONY_NARROWBAND.sample_rate_hz == 8_000
    assert TELEPHONY_NARROWBAND.encoding is AudioEncoding.MULAW
    assert SPEECH_WIDEBAND.sample_rate_hz == 16_000
    assert SPEECH_WIDEBAND.encoding is AudioEncoding.PCM_S16LE
    assert TELEPHONY_NARROWBAND != SPEECH_WIDEBAND


def test_formats_compare_by_value() -> None:
    assert AudioFormat(AudioEncoding.MULAW, 8_000) == TELEPHONY_NARROWBAND


def test_a_frame_knows_its_length_and_format() -> None:
    frame = AudioFrame(b"\x00\x01\x02\x03", SPEECH_WIDEBAND)
    assert len(frame) == 4
    assert frame.format is SPEECH_WIDEBAND


def test_an_empty_frame_is_rejected() -> None:
    with pytest.raises(InvariantError):
        AudioFrame(b"", SPEECH_WIDEBAND)


def test_a_frame_never_shows_its_samples() -> None:
    frame = AudioFrame(b"secret-sounding-audio", SPEECH_WIDEBAND)
    rendered = repr(frame)
    assert "secret-sounding-audio" not in rendered
    assert "21 bytes" in rendered
    assert "16000Hz" in rendered
