"""The clock, the wait and the dice a session uses, injected.

Injected so that a test of a four-attempt backoff takes no time and gives the same answer every
run, and so that a latency test measures the session rather than the machine running it.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# A reading from a clock that only moves forward, in seconds. Monotonic, because a latency
# measured against the wall clock goes negative the moment the machine corrects its time.
type MonotonicClock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Timekeeping:
    """How a session tells the time, waits, and draws a random number."""

    clock: MonotonicClock = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    # Jitter spreads retries apart and guards nothing, so a predictable generator is fine.
    draw: Callable[[], float] = random.random
