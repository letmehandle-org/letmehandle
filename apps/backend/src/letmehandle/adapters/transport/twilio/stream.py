"""One assistant leg's media stream, and the source and sink a conversation runs over.

The stream belongs to a leg, not to a call: the assistant can leave and be brought back, and
each time it is a new leg with a new websocket. A source or sink handed out for a call follows
whichever assistant leg is current when it is used, so a conversation started after the
assistant rejoins needs nothing new from anybody.

Two directions, each with its own bound. Arriving audio waits in a short queue, and when that
fills the oldest is dropped: a live call cannot be told to slow down, and audio a listener was
too slow to hear is audio nobody wants late. Departing audio is paced against the clock, so the
sink holds no more than a few hundred milliseconds ahead of what the call has played — which is
what the sink contract asks, because audio handed over is audio the speech session counts as
heard.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, Protocol

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.transport.twilio.media import clear_message, media_message
from letmehandle.adapters.transport.twilio.rest import PROVIDER
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND, AudioFormat, AudioFrame
from letmehandle.domain.ports.audio_io import AudioSink, AudioSource

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

# About five seconds of call audio at twenty milliseconds a frame.
INBOUND_QUEUE_FRAMES: Final = 250
# How far ahead of the call's playback the sink may run before a writer waits.
PLAYBACK_LEAD_SECONDS: Final = 0.4
# How long a writer waits for the assistant's stream to connect before giving up on it.
STREAM_CONNECT_TIMEOUT_SECONDS: Final = 15.0

# A wait shorter than this is not worth scheduling, and keeps rounding from causing one.
_SHORTEST_WAIT_SECONDS: Final = 0.001

_BYTES_PER_SECOND: Final = TELEPHONY_NARROWBAND.sample_rate_hz  # one μ-law byte per sample


class MediaSocketClosedError(Exception):
    """The websocket went away while something was being sent on it."""


class MediaSocket(Protocol):
    """One accepted websocket carrying the provider's media stream protocol as text."""

    async def receive(self) -> str | None:
        """The next text frame, or `None` once the socket has closed."""

    async def send(self, text: str) -> None:
        """Send a text frame. Raises `MediaSocketClosedError` once the socket has gone."""

    async def close(self) -> None:
        """Close the socket. Safe to call more than once, and after the other end has gone."""


class MediaStream:
    """The media of one assistant leg, from before its socket connects until after it closes."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        is_muted: Callable[[], bool] = lambda: False,
    ) -> None:
        self._monotonic = monotonic
        self._sleep = sleep
        self._is_muted = is_muted
        self._frames: asyncio.Queue[AudioFrame | None] = asyncio.Queue(INBOUND_QUEUE_FRAMES)
        self._settled = asyncio.Event()
        self._socket: MediaSocket | None = None
        self._stream_sid: str | None = None
        self._ended = False
        self._play_until = 0.0
        self._converter: tuple[AudioFormat, AudioConverter] | None = None
        self.dropped_frames = 0

    @property
    def is_connected(self) -> bool:
        return self._socket is not None and not self._ended

    @property
    def has_ended(self) -> bool:
        return self._ended

    def attach(self, socket: MediaSocket, stream_sid: str) -> None:
        """The leg's websocket has started streaming."""
        self._socket = socket
        self._stream_sid = stream_sid
        self._settled.set()

    def receive(self, audio: bytes) -> None:
        """Audio from the call. The oldest is dropped when the listener has fallen behind."""
        if self._ended or not audio:
            return
        if self._frames.full():
            self._frames.get_nowait()
            self.dropped_frames += 1
        self._frames.put_nowait(AudioFrame(audio, TELEPHONY_NARROWBAND))

    async def end(self) -> None:
        """Nothing more will arrive or be sent. Closes the socket. Safe to call more than once."""
        if self._ended:
            return
        self._ended = True
        self._settled.set()
        if self._frames.full():
            # Room for the end marker matters more than one frame nobody will now hear.
            self._frames.get_nowait()
        self._frames.put_nowait(None)
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()

    async def frames(self) -> AsyncIterator[AudioFrame]:
        while True:
            frame = await self._frames.get()
            if frame is None:
                return
            yield frame

    async def play(self, frame: AudioFrame) -> None:
        socket, stream_sid = await self._connected()
        audio = self._convert(frame)
        if not audio:
            return
        now = self._monotonic()
        self._play_until = max(now, self._play_until) + len(audio) / _BYTES_PER_SECOND
        # Listening only: the conference already stops anyone hearing this leg, and sending
        # audio nobody will hear is load for nothing. It is still paced as if it were played,
        # because the speech session counts what the sink takes as heard.
        if not self._is_muted():
            await self._send(socket, media_message(stream_sid, audio))
        ahead = self._play_until - self._monotonic() - PLAYBACK_LEAD_SECONDS
        if ahead > _SHORTEST_WAIT_SECONDS:
            await self._sleep(ahead)

    async def discard(self) -> None:
        if not self.is_connected or self._socket is None or self._stream_sid is None:
            return
        self._play_until = self._monotonic()
        if self._converter is not None:
            self._converter[1].reset()
        await self._send(self._socket, clear_message(self._stream_sid))

    async def _connected(self) -> tuple[MediaSocket, str]:
        try:
            async with asyncio.timeout(STREAM_CONNECT_TIMEOUT_SECONDS):
                await self._settled.wait()
        except TimeoutError:
            raise ProviderError(
                PROVIDER, "the assistant's audio stream did not connect in time", retryable=True
            ) from None
        if self._ended or self._socket is None or self._stream_sid is None:
            raise ProviderError(PROVIDER, "the assistant's audio stream has ended", retryable=False)
        return self._socket, self._stream_sid

    async def _send(self, socket: MediaSocket, text: str) -> None:
        try:
            await socket.send(text)
        except MediaSocketClosedError:
            raise ProviderError(
                PROVIDER, "the assistant's audio stream has ended", retryable=False
            ) from None

    def _convert(self, frame: AudioFrame) -> bytes:
        if self._converter is None or self._converter[0] != frame.format:
            self._converter = (frame.format, AudioConverter(frame.format, TELEPHONY_NARROWBAND))
        return self._converter[1].convert(frame.data)


class StreamLocator(Protocol):
    """Finds the stream of whichever assistant leg is current on a call."""

    def current_stream(self) -> MediaStream | None:
        """The current assistant leg's stream, or `None` when no assistant is on the call."""


class CallAudioSource(AudioSource):
    """The caller's audio, from the call's current assistant leg."""

    def __init__(self, locator: StreamLocator) -> None:
        self._locator = locator

    @property
    def format(self) -> AudioFormat:
        return TELEPHONY_NARROWBAND

    async def frames(self) -> AsyncIterator[AudioFrame]:
        stream = self._locator.current_stream()
        if stream is None:
            return
        async for frame in stream.frames():
            yield frame


class CallAudioSink(AudioSink):
    """The assistant's voice, onto the call's current assistant leg, converted at this edge."""

    def __init__(self, locator: StreamLocator) -> None:
        self._locator = locator

    @property
    def format(self) -> AudioFormat:
        return TELEPHONY_NARROWBAND

    async def write(self, frame: AudioFrame) -> None:
        stream = self._locator.current_stream()
        if stream is None:
            raise ProviderError(PROVIDER, "no assistant is on the call", retryable=False)
        await stream.play(frame)

    async def discard(self) -> None:
        stream = self._locator.current_stream()
        if stream is not None:
            await stream.discard()
