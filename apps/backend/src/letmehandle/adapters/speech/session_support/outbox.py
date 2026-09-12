"""What a session holds for its consumer, and why reading the service never waits on it.

A consumer playing through a speaker takes audio at the speed of speech, and the service sends
it faster than that. A reader that waited for room before reading on would leave everything
behind the queued audio unread — the interruption that should silence it, and the ping that has
to be answered or the service hangs up — until the speaker caught up, seconds later.

So events are held without waiting, and only audio is bounded: by how long it would take to
play, rather than by how many pieces it came in, because the service decides the size of a piece.
The bound is far beyond one reply, so it is reached only by a consumer that has stopped taking
audio altogether, and only then does the reader stop reading. Everything else is small, and
arrives no faster than people speak.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.ports.speech import SpeechEvent


@dataclass(frozen=True, slots=True)
class _Held:
    """An event waiting for the consumer, and whether it is the agent's speech to discard."""

    event: SpeechEvent
    spoken: bool
    audio_seconds: float


@dataclass(frozen=True, slots=True)
class _Ended:
    """Nothing more will arrive."""


_END: Final = _Ended()


class Outbox:
    """The events of one session, in order, with the agent's audio bounded by its duration."""

    def __init__(self, audio_ceiling_seconds: float) -> None:
        self._ceiling = audio_ceiling_seconds
        self._held: deque[_Held | _Ended] = deque()
        self._audio_seconds = 0.0
        self._arrived = asyncio.Event()
        self._room = asyncio.Event()
        self._room.set()

    def put(self, event: SpeechEvent, *, spoken: bool = False, audio_seconds: float = 0.0) -> None:
        """Hold an event for the consumer. Never waits: `room_for_audio` is the one that does.

        `spoken` marks the agent's speech — its audio and the start of it — which is what an
        interruption discards. Words are not marked: what the agent began to say was said.
        """
        self._held.append(_Held(event, spoken, audio_seconds))
        self._account(audio_seconds)
        self._arrived.set()

    async def room_for_audio(self) -> None:
        """Wait until the audio held is below the ceiling."""
        while self._audio_seconds >= self._ceiling:
            self._room.clear()
            await self._room.wait()

    def discard_speech(self) -> None:
        """Drop the agent's speech that the consumer has not taken."""
        kept = [each for each in self._held if isinstance(each, _Ended) or not each.spoken]
        self._held = deque(kept)
        self._account(-self._audio_seconds)

    def end(self) -> None:
        """Nothing more will be put. Everything already held is still delivered."""
        self._held.append(_END)
        self._arrived.set()

    def abandon(self) -> None:
        """End now, dropping whatever the consumer had not taken."""
        self._held.clear()
        self._account(-self._audio_seconds)
        self.end()

    async def events(self) -> AsyncIterator[SpeechEvent]:
        """Everything held, in order, until the end."""
        while True:
            while not self._held:
                self._arrived.clear()
                await self._arrived.wait()
            held = self._held[0]
            if isinstance(held, _Ended):
                # Left in place, so that every later iterator ends too.
                return
            self._held.popleft()
            self._account(-held.audio_seconds)
            yield held.event

    def _account(self, audio_seconds: float) -> None:
        self._audio_seconds = max(0.0, self._audio_seconds + audio_seconds)
        if self._audio_seconds < self._ceiling:
            self._room.set()
