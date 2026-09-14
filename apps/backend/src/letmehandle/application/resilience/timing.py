"""Measuring and bounding a wait on something a call depends on."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta


class Stopwatch:
    """Seconds since it was made, on the event loop's clock, which never runs backwards."""

    def __init__(self) -> None:
        self._started = asyncio.get_running_loop().time()

    @property
    def seconds(self) -> float:
        return asyncio.get_running_loop().time() - self._started


async def within[T](bound: timedelta, work: Callable[[], Awaitable[T]]) -> T:
    """`work`, or `TimeoutError` once `bound` has passed, raised inside any circuit around it."""
    async with asyncio.timeout(bound.total_seconds()):
        return await work()
