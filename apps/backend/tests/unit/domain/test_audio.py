"""A frame that does not know what it is becomes a frame somebody converts twice."""

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
    # Named so that adapters agree rather than each writing the numbers out, and so that a
    # mismatch between them is visible here rather than as silence on a call.
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
    # An empty frame is either a bug upstream or a silent gap presented as audio. Neither is
    # something to pass along.
    with pytest.raises(InvariantError):
        AudioFrame(b"", SPEECH_WIDEBAND)


def test_a_frame_never_shows_its_samples() -> None:
    # The samples are somebody's voice. A frame's repr turns up in test output and in exception
    # context, and this project promises that voice is not written down.
    frame = AudioFrame(b"secret-sounding-audio", SPEECH_WIDEBAND)
    rendered = repr(frame)
    assert "secret-sounding-audio" not in rendered
    assert "21 bytes" in rendered
    assert "16000Hz" in rendered
