"""Where a conversation's audio comes from, and where the reply goes.

Neither side knows what it is attached to. A phone call, a microphone and a file are the same
thing from here, which is the property the speech layer depends on: one of the transports this
product supports cannot supply call audio at all, and a speech layer that assumed a call would
become a platform branch somewhere else.
"""

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
        """What every frame this source yields is in.

        Declared up front rather than discovered from the first frame, because a speech session
        is opened with its input format and the first frame arrives after that.
        """

    @abstractmethod
    def frames(self) -> AsyncIterator[AudioFrame]:
        """Frames as they arrive. The iterator ending means the speaker has gone."""


class AudioSink(ABC):
    """Where the assistant's voice is played."""

    @property
    @abstractmethod
    def format(self) -> AudioFormat:
        """What this sink plays. Conversion to it is the writer's job, not the sink's."""

    @abstractmethod
    async def write(self, frame: AudioFrame) -> None:
        """Play a frame, after whatever is already waiting to be played."""

    @abstractmethod
    async def discard(self) -> None:
        """Drop everything written and not yet played.

        The half of interruption that happens here. A model that stops producing while this
        sink plays out what it had buffered still talks over the person who interrupted it.
        """
