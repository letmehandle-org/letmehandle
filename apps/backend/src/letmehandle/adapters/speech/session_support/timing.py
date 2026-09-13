"""The clock, the wait and the random draw a session uses, injected so tests control them."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Seconds from a clock that only moves forward, never the wall clock.
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Timekeeping:
    """How a session tells the time, waits, and draws a random number."""

    clock: MonotonicClock = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # Jitter guards nothing, so a predictable generator is fine.
    draw: Callable[[], float] = random.random
