"""Audio that is not a call: a tone generated on the spot, and a speaker that only takes notes.

Nothing is read from or written to disk. The tone is computed when a frame is asked for, which
keeps recorded audio out of the repository and means a test's audio is exactly what it says.
"""

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
    """The `index`th frame of a continuous wideband sine tone.

    The phase carries on from the previous frame, so consecutive frames join without a click; a
    test comparing what went in with what came out is comparing a real signal.
    """
    first = index * SAMPLES_PER_FRAME
    rate = SPEECH_WIDEBAND.sample_rate_hz
    samples = [
        round(_AMPLITUDE * math.sin(2 * math.pi * _FREQUENCY_HZ * n / rate))
        for n in range(first, first + SAMPLES_PER_FRAME)
    ]
    return AudioFrame(struct.pack(f"<{len(samples)}h", *samples), SPEECH_WIDEBAND)


class ToneSource(AudioSource, AsyncIterator[AudioFrame]):
    """A speaker who hums.

    `frames` is how many frames to say, or `None` to go on until hung up. With `stays_open`, the
    speaker stays on the line after the last frame until `hang_up`, so a test can decide when
    the source ends rather than racing it.
    """

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

    # The source is its own iterator rather than a generator. A transport reading a socket is
    # usually shaped this way, and a consumer that only cleaned up after generators would pass
    # every test written with one.
    def __aiter__(self) -> AsyncIterator[AudioFrame]:
        return self

    async def __anext__(self) -> AudioFrame:
        if self._gone.is_set() or (self._limit is not None and self.taken >= self._limit):
            self.silent.set()
            if self._stays_open:
                await self._gone.wait()
            raise StopAsyncIteration
        # A real source waits between frames for the next one to arrive. Without this a source
        # that never ends would never let anything else run.
        await asyncio.sleep(0)
        frame = tone_frame(self.taken)
        self.taken += 1
        return frame


class RecordingSink(AudioSink):
    """Plays nothing, and remembers everything it was asked to play and when it was cleared.

    `hold` makes playback slow in the way that matters: a write does not return until `release`,
    exactly like a speaker whose buffer is full.
    """

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
