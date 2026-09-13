"""Events held for a consumer without making the reader wait, audio bounded by its duration."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from letmehandle.domain.ports.speech import SpeechEvent


@dataclass(frozen=True, slots=True)
class _Held:
    """An event waiting for the consumer, and whether it is the agent's speech to discard."""

    event: SpeechEvent
    spoken: bool
    audio_seconds: float
    taken: Callable[[], None] | None


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

    def put(
        self,
        event: SpeechEvent,
        *,
        spoken: bool = False,
        audio_seconds: float = 0.0,
        taken: Callable[[], None] | None = None,
    ) -> None:
        """Hold an event; `spoken` is discarded on interruption, `taken` runs when it is taken."""
        self._held.append(_Held(event, spoken, audio_seconds, taken))
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
        """Nothing more will be put; everything already held is still delivered."""
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
                # Left in place, so every later iterator ends too.
                return
            self._held.popleft()
            self._account(-held.audio_seconds)
            if held.taken is not None:
                held.taken()
            yield held.event

    def _account(self, audio_seconds: float) -> None:
        self._audio_seconds = max(0.0, self._audio_seconds + audio_seconds)
        if self._audio_seconds < self._ceiling:
            self._room.set()
