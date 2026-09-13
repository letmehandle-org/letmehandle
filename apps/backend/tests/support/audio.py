"""Generated tones, a humming audio source and a note-taking audio sink, with nothing on disk."""

from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import AsyncIterator
from typing import Final

from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.ports.audio_io import AudioSink, AudioSource

# 20 ms of wideband audio: the frame size realtime speech services commonly expect.
SAMPLES_PER_FRAME: Final = SPEECH_WIDEBAND.sample_rate_hz // 50
_AMPLITUDE: Final = 0.25 * 32_767
_FREQUENCY_HZ: Final = 440.0


def tone_frame(index: int) -> AudioFrame:
    """The `index`th frame of a continuous wideband sine tone, phase-continuous across frames."""
    first = index * SAMPLES_PER_FRAME
    rate = SPEECH_WIDEBAND.sample_rate_hz
    samples = [
        round(_AMPLITUDE * math.sin(2 * math.pi * _FREQUENCY_HZ * n / rate))
        for n in range(first, first + SAMPLES_PER_FRAME)
    ]
    return AudioFrame(struct.pack(f"<{len(samples)}h", *samples), SPEECH_WIDEBAND)


class ToneSource(AudioSource, AsyncIterator[AudioFrame]):
    """A humming source of `frames` frames, or endless; with `stays_open`, open until `hang_up`."""

    def __init__(self, *, frames: int | None, stays_open: bool = False) -> None:
        self._limit = frames
        self._stays_open = stays_open
        self._gone = asyncio.Event()
        self.taken = 0
        self.silent = asyncio.Event()

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    def frames(self) -> AsyncIterator[AudioFrame]:
        return self

    def hang_up(self) -> None:
        self._gone.set()

    def said(self) -> list[AudioFrame]:
        """Every frame taken so far, regenerated, for comparing with what came back."""
        return [tone_frame(index) for index in range(self.taken)]

        # Its own iterator rather than a generator, as a transport reading a socket is.

    def __aiter__(self) -> AsyncIterator[AudioFrame]:
        return self

    async def __anext__(self) -> AudioFrame:
        if self._gone.is_set() or (self._limit is not None and self.taken >= self._limit):
            self.silent.set()
            if self._stays_open:
                await self._gone.wait()
            raise StopAsyncIteration
            # Yields between frames, as a real source waiting for audio does.
        await asyncio.sleep(0)
        frame = tone_frame(self.taken)
        self.taken += 1
        return frame


class RecordingSink(AudioSink):
    """Remembers what it played and when it was cleared; `hold` blocks writes until `release`."""

    def __init__(self) -> None:
        self._flowing = asyncio.Event()
        self._flowing.set()
        self._progress = asyncio.Condition()
        self.written: list[AudioFrame] = []
        self.discarded_after: list[int] = []
        self.held = asyncio.Event()

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    async def write(self, frame: AudioFrame) -> None:
        if not self._flowing.is_set():
            self.held.set()
        await self._flowing.wait()
        async with self._progress:
            self.written.append(frame)
            self._progress.notify_all()

    async def discard(self) -> None:
        self.discarded_after.append(len(self.written))

    def hold(self) -> None:
        self._flowing.clear()

    def release(self) -> None:
        self._flowing.set()

    async def until_written(self, count: int) -> None:
        async with self._progress:
            await self._progress.wait_for(lambda: len(self.written) >= count)
