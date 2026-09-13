"""Where a conversation's audio comes from and where the reply goes, whatever they attach to."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.models.audio import AudioFormat, AudioFrame


class AudioSource(ABC):
    """Audio arriving from whoever is speaking to the assistant."""

    @property
    @abstractmethod
    def format(self) -> AudioFormat:
        """The format of every frame this source yields, known before the first arrives."""

    @abstractmethod
    def frames(self) -> AsyncIterator[AudioFrame]:
        """Frames as they arrive; the iterator ending means the speaker has gone."""


class AudioSink(ABC):
    """Where the assistant's voice is played."""

    @property
    @abstractmethod
    def format(self) -> AudioFormat:
        """What this sink plays, converting frames of any other format at its own edge."""

    @abstractmethod
    async def write(self, frame: AudioFrame) -> None:
        """Queue a frame to play, making the writer wait once a few hundred ms are queued."""

    @abstractmethod
    async def discard(self) -> None:
        """Drop everything written and not yet played."""
