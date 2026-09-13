"""Audio frames that carry their own format; converting between formats is an adapter's job."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from letmehandle.domain.errors import InvariantError


class AudioEncoding(StrEnum):
    """How the samples in a frame are represented."""

    PCM_S16LE = "pcm_s16le"
    MULAW = "mulaw"
    ALAW = "alaw"
    OPUS = "opus"


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """An encoding, a sample rate and a channel count, together."""

    encoding: AudioEncoding
    sample_rate_hz: int
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise InvariantError("a sample rate must be positive")
        if self.channels <= 0:
            raise InvariantError("a frame must have at least one channel")

    def __str__(self) -> str:
        return f"{self.encoding} {self.sample_rate_hz}Hz {self.channels}ch"


# The formats at the product's two edges: a telephone line, and a speech model.
TELEPHONY_NARROWBAND = AudioFormat(AudioEncoding.MULAW, 8_000)
SPEECH_WIDEBAND = AudioFormat(AudioEncoding.PCM_S16LE, 16_000)


@dataclass(frozen=True, slots=True)
class AudioFrame:
    """A piece of audio, and what it is."""

    data: bytes
    format: AudioFormat

    def __post_init__(self) -> None:
        if not self.data:
            raise InvariantError("an audio frame carries no audio")

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        """Length and format, never the samples."""
        return f"AudioFrame({len(self.data)} bytes, {self.format})"
