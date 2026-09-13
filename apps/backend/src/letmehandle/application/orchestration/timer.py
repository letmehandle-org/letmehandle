"""A bounded wait a run owns, whose running out reaches the run as an input (D-029)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from letmehandle.application.orchestration.inputs import Input


class Timer:
    """One wait a run arms, cancels and releases; what it posts names the arming that ran out."""

    def __init__(self, post: Callable[[Input], None]) -> None:
        self._post = post
        self._task: asyncio.Task[None] | None = None
        self._generation = 0

    def arm(self, after: timedelta, expired: Callable[[int], Input]) -> None:
        """Wait `after`, replacing any wait already armed, then post `expired` of this arming."""
        self.cancel()
        self._generation += 1
        generation = self._generation

        async def wait() -> None:
            await asyncio.sleep(after.total_seconds())
            self._post(expired(generation))

        self._task = asyncio.get_running_loop().create_task(wait())

    def cancel(self) -> None:
        """Disarm, so an expiry already posted is recognised as stale by its generation."""
        self._generation += 1
        if self._task is not None:
            self._task.cancel()

    def is_current(self, generation: int) -> bool:
        """Whether an expiry of `generation` is from the wait still armed."""
        return generation == self._generation

    async def release(self) -> None:
        """Disarm and wait for the wait's task to go."""
        self.cancel()
        task, self._task = self._task, None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
