"""Audio conversion, proven on signals generated here and never on a recording (D-013)."""

from __future__ import annotations

import math
import struct
from itertools import pairwise

import pytest

from letmehandle.adapters.audio.conversion import (
    AudioConverter,
    UnsupportedConversionError,
    can_convert,
    pcm_duration_ms,
)
from letmehandle.domain.models.audio import (
    SPEECH_WIDEBAND,
    TELEPHONY_NARROWBAND,
    AudioEncoding,
    AudioFormat,
)

WIRE = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)
NARROWBAND_LINEAR = AudioFormat(AudioEncoding.PCM_S16LE, 8_000)
ALAW_NARROWBAND = AudioFormat(AudioEncoding.ALAW, 8_000)


def tone(frequency_hz: float, rate: int, seconds: float, amplitude: int = 12_000) -> list[int]:
    return [
        round(amplitude * math.sin(2 * math.pi * frequency_hz * n / rate))
        for n in range(int(rate * seconds))
    ]


def pcm(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def samples_of(data: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(data) // 2}h", data))


def zero_crossings(samples: list[int]) -> int:
    return sum(1 for a, b in pairwise(samples) if (a < 0) != (b < 0))


def signal_to_noise_db(reference: list[int], measured: list[int]) -> float:
    signal = sum(s * s for s in reference)
    noise = sum((s - m) ** 2 for s, m in zip(reference, measured, strict=True))
    return 10 * math.log10(signal / noise)


def test_the_same_format_passes_through_untouched() -> None:
    data = pcm(tone(440, 24_000, 0.02))
    assert AudioConverter(WIRE, WIRE).convert(data) == data


@pytest.mark.parametrize(("source_rate", "target_rate"), [(16_000, 24_000), (24_000, 8_000)])
def test_resampling_keeps_duration_and_pitch(source_rate: int, target_rate: int) -> None:
    # A one-second 300 Hz tone must still last a second and still be 300 Hz: the two ways a
    # resampler gets the ratio backwards are a chipmunk and a slur.
    source = tone(300, source_rate, 1.0)
    converted = samples_of(
        AudioConverter(
            AudioFormat(AudioEncoding.PCM_S16LE, source_rate),
            AudioFormat(AudioEncoding.PCM_S16LE, target_rate),
        ).convert(pcm(source))
    )
    assert abs(len(converted) - target_rate) <= 1
    assert abs(zero_crossings(converted) - zero_crossings(source)) <= 2


def test_resampling_in_pieces_matches_resampling_at_once() -> None:
    # Audio arrives in frames. A resampler that restarted at each frame edge would click there.
    source = pcm(tone(440, 16_000, 0.2))
    whole = AudioConverter(SPEECH_WIDEBAND, WIRE).convert(source)

    in_pieces = AudioConverter(SPEECH_WIDEBAND, WIRE)
    pieces = b"".join(in_pieces.convert(source[i : i + 320]) for i in range(0, len(source), 320))

    assert pieces == whole


def test_a_sample_split_across_pieces_is_put_back_together() -> None:
    # A service is free to end a delta in the middle of a sample.
    source = pcm(tone(440, 24_000, 0.02))
    converter = AudioConverter(WIRE, SPEECH_WIDEBAND)
    split = converter.convert(source[:101]) + converter.convert(source[101:])
    assert split == AudioConverter(WIRE, SPEECH_WIDEBAND).convert(source)


def test_reset_forgets_a_half_sample_left_over() -> None:
    converter = AudioConverter(WIRE, SPEECH_WIDEBAND)
    converter.convert(b"\x01")
    converter.reset()
    fresh = AudioConverter(WIRE, SPEECH_WIDEBAND)
    source = pcm(tone(440, 24_000, 0.02))
    assert converter.convert(source) == fresh.convert(source)


@pytest.mark.parametrize("companded", [TELEPHONY_NARROWBAND, ALAW_NARROWBAND])
def test_telephony_audio_survives_a_round_trip(companded: AudioFormat) -> None:
    # G.711 is lossy by design; what matters is that speech comes back as speech. Better than
    # 30 dB on a mid-level tone is what the standard gives, and a sign or segment error would
    # leave it near zero.
    source = tone(400, 8_000, 0.5)
    encoded = AudioConverter(NARROWBAND_LINEAR, companded).convert(pcm(source))
    decoded = samples_of(AudioConverter(companded, NARROWBAND_LINEAR).convert(encoded))

    assert len(encoded) == len(source)
    assert signal_to_noise_db(source, decoded) > 30


@pytest.mark.parametrize(
    ("companded", "silence_code"), [(TELEPHONY_NARROWBAND, 0xFF), (ALAW_NARROWBAND, 0xD5)]
)
def test_silence_is_the_code_each_standard_reserves_for_it(
    companded: AudioFormat, silence_code: int
) -> None:
    encoded = AudioConverter(NARROWBAND_LINEAR, companded).convert(pcm([0] * 4))
    assert encoded == bytes([silence_code] * 4)
    decoded = samples_of(AudioConverter(companded, NARROWBAND_LINEAR).convert(encoded))
    assert all(abs(sample) <= 8 for sample in decoded)


@pytest.mark.parametrize("companded", [TELEPHONY_NARROWBAND, ALAW_NARROWBAND])
def test_the_loudest_samples_keep_their_sign(companded: AudioFormat) -> None:
    extremes = [32_767, -32_768, 32_767, -32_768]
    encoded = AudioConverter(NARROWBAND_LINEAR, companded).convert(pcm(extremes))
    decoded = samples_of(AudioConverter(companded, NARROWBAND_LINEAR).convert(encoded))
    assert [sample > 0 for sample in decoded] == [True, False, True, False]
    assert all(abs(sample) > 30_000 for sample in decoded)


def test_a_phone_call_becomes_wire_audio_of_the_right_length() -> None:
    twenty_ms_of_call = bytes([0xFF] * 160)
    converted = AudioConverter(TELEPHONY_NARROWBAND, WIRE).convert(twenty_ms_of_call)
    # 160 samples at 8 kHz is 480 at 24 kHz, give or take the one the interpolator waits for.
    assert abs(len(converted) // 2 - 478) <= 2


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (AudioFormat(AudioEncoding.OPUS, 48_000), WIRE),
        (WIRE, AudioFormat(AudioEncoding.OPUS, 48_000)),
        (AudioFormat(AudioEncoding.PCM_S16LE, 16_000, channels=2), WIRE),
        (WIRE, AudioFormat(AudioEncoding.PCM_S16LE, 16_000, channels=2)),
    ],
)
def test_what_cannot_be_converted_is_refused(source: AudioFormat, target: AudioFormat) -> None:
    assert not can_convert(source, target)
    with pytest.raises(UnsupportedConversionError):
        AudioConverter(source, target)


def test_duration_is_measured_from_the_samples() -> None:
    assert pcm_duration_ms(bytes(480), 24_000) == pytest.approx(10.0)
