"""Audio, as the domain sees it.

A frame carries its own encoding and sample rate. That is the whole point: a call transport
typically speaks narrowband telephony audio, a speech model typically wants wideband linear
audio, and if a frame did not say which it was, every function touching one would have to be
told separately — and one of them would be told wrong.

Conversion is an adapter's job, at its own edge. The domain never resamples; it only insists
that a frame knows what it is.
"""

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
    """An encoding, a sample rate and a channel count, together.

    Together because they are only meaningful together: eight thousand samples per second says
    nothing without knowing what a sample is.
    """

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


# The two that appear at the edges of this product, named so that adapters agree on them
# rather than each writing the numbers out.
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
        """Length and format, never the samples.

        A frame's repr appears in test failures and in exception context. The samples are
        somebody's voice, and a few kilobytes of it in a log is exactly the disclosure this
        project promises not to make.
        """
        return f"AudioFrame({len(self.data)} bytes, {self.format})"
