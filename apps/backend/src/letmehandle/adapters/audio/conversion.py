"""Stateful mono conversion between linear PCM, μ-law and A-law at any rate, in plain Python."""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, Final

from letmehandle.domain.models.audio import AudioEncoding

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.audio import AudioFormat

_SAMPLE_WIDTH: Final = 2
_S16_MAX: Final = 32_767

# G.711 constants: the μ-law bias and clip, and the A-law bit toggle.
_MULAW_BIAS: Final = 0x84
_MULAW_CLIP: Final = 32_635
_ALAW_TOGGLE: Final = 0x55

_CONVERTIBLE: Final = frozenset({AudioEncoding.PCM_S16LE, AudioEncoding.MULAW, AudioEncoding.ALAW})


class UnsupportedConversionError(Exception):
    """Audio in a format this module cannot convert to the one required."""


def can_convert(source: AudioFormat, target: AudioFormat) -> bool:
    """Whether mono audio in `source` can be turned into mono `target`."""
    return (
        source.channels == 1
        and target.channels == 1
        and source.encoding in _CONVERTIBLE
        and target.encoding in _CONVERTIBLE
    )


def pcm_duration_ms(data: bytes, sample_rate_hz: int) -> float:
    """How long a piece of 16-bit mono linear audio lasts."""
    return len(data) / _SAMPLE_WIDTH / sample_rate_hz * 1_000


class AudioConverter:
    """One direction of one stream, from a source format to a target format."""

    def __init__(self, source: AudioFormat, target: AudioFormat) -> None:
        if not can_convert(source, target):
            raise UnsupportedConversionError(f"cannot convert {source} to {target}")
        self._source = source
        self._target = target
        self._carry = b""
        self._resampler = _Resampler(source.sample_rate_hz, target.sample_rate_hz)

    def convert(self, data: bytes) -> bytes:
        """The converted audio. May be empty when too little arrived to make a sample."""
        if self._source == self._target:
            return data
        samples = self._decode(data)
        return _encode(self._resampler.process(samples), self._target.encoding)

    def reset(self) -> None:
        """Forget the stream so far, for a deliberate break such as an interruption."""
        self._carry = b""
        self._resampler = _Resampler(self._source.sample_rate_hz, self._target.sample_rate_hz)

    def _decode(self, data: bytes) -> list[int]:
        if self._source.encoding is AudioEncoding.PCM_S16LE:
            data = self._carry + data
            usable = len(data) - len(data) % _SAMPLE_WIDTH
            self._carry = data[usable:]
            return list(struct.unpack(f"<{usable // _SAMPLE_WIDTH}h", data[:usable]))
        table = _MULAW_DECODE if self._source.encoding is AudioEncoding.MULAW else _ALAW_DECODE
        return [table[code] for code in data]


class _Resampler:
    """Linear interpolation, with no low-pass filter, that keeps its position between pieces."""

    def __init__(self, source_rate: int, target_rate: int) -> None:
        self._step = source_rate / target_rate
        self._identity = source_rate == target_rate
        # Position measured from `_previous`, the last sample of the previous piece.
        self._position = 0.0
        self._previous: int | None = None

    def process(self, samples: Sequence[int]) -> list[int]:
        if self._identity or not samples:
            return list(samples)
        stream = list(samples) if self._previous is None else [self._previous, *samples]
        last = len(stream) - 1
        output: list[int] = []
        position = self._position
        while position < last:
            index = int(position)
            fraction = position - index
            before = stream[index]
            output.append(round(before + (stream[index + 1] - before) * fraction))
            position += self._step
        self._position = position - last
        self._previous = stream[last]
        return output


def _encode(samples: Sequence[int], encoding: AudioEncoding) -> bytes:
    if encoding is AudioEncoding.PCM_S16LE:
        return struct.pack(f"<{len(samples)}h", *samples)
    encode = _mulaw_encode if encoding is AudioEncoding.MULAW else _alaw_encode
    return bytes(encode(sample) for sample in samples)


def _mulaw_decode(code: int) -> int:
    code = ~code & 0xFF
    exponent = (code >> 4) & 0x07
    magnitude = (((code & 0x0F) << 3) + _MULAW_BIAS) << exponent
    sample = magnitude - _MULAW_BIAS
    return -sample if code & 0x80 else sample


def _mulaw_encode(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    biased = min(abs(sample), _MULAW_CLIP) + _MULAW_BIAS
    exponent = max(biased.bit_length() - 8, 0)
    mantissa = (biased >> (exponent + 3)) & 0x0F
    return ~(sign | exponent << 4 | mantissa) & 0xFF


def _alaw_decode(code: int) -> int:
    code ^= _ALAW_TOGGLE
    exponent = (code >> 4) & 0x07
    step = (code & 0x0F) << 4
    # The lowest segment is linear; every other one doubles the step of the one below it.
    magnitude = step + 8 if exponent == 0 else (step + 0x108) << (exponent - 1)
    return magnitude if code & 0x80 else -magnitude


def _alaw_encode(sample: int) -> int:
    # A-law marks a positive sample with the sign bit set, the opposite of μ-law.
    sign = 0x80 if sample >= 0 else 0
    thirteen_bit = min(sample if sample >= 0 else -sample - 1, _S16_MAX) >> 3
    if thirteen_bit < 0x20:
        exponent, mantissa = 0, thirteen_bit >> 1
    else:
        exponent = thirteen_bit.bit_length() - 5
        mantissa = (thirteen_bit >> exponent) & 0x0F
    return (sign | exponent << 4 | mantissa) ^ _ALAW_TOGGLE


# Decoding is a lookup, because there are only 256 codes and a frame is all lookups.
_MULAW_DECODE: Final = tuple(_mulaw_decode(code) for code in range(256))
_ALAW_DECODE: Final = tuple(_alaw_decode(code) for code in range(256))
