"""How long a piece of wire audio lasts, in the linear and G.711 formats the protocols carry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.models.audio import AudioEncoding

if TYPE_CHECKING:
    from letmehandle.domain.models.audio import AudioFormat


def duration_ms(audio: bytes, audio_format: AudioFormat) -> float:
    """The duration of `audio`: two bytes a sample for 16-bit linear audio, one for G.711."""
    width = 2 if audio_format.encoding is AudioEncoding.PCM_S16LE else 1
    return len(audio) / width / audio_format.sample_rate_hz * 1000
