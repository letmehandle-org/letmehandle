"""A rate limiter that counts in this process; `is_shared` is false."""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from letmehandle.domain.ports.rate_limit import RateLimitDecision, RateLimiter

if TYPE_CHECKING:
    from datetime import timedelta

    from letmehandle.domain.ports.clock import Clock


class InMemoryRateLimiter(RateLimiter):
    """A sliding window per key, held in this process."""

    def __init__(self, clock: Clock, *, max_keys: int = 100_000) -> None:
        self._clock = clock
        self._attempts: defaultdict[str, deque[float]] = defaultdict(deque)
        # Bounded, since attacker-chosen keys could otherwise exhaust memory.
        self._max_keys = max_keys

    @property
    def is_shared(self) -> bool:
        """False. Several processes each get their own counters."""
        return False

    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        now = self._clock.now().timestamp()
        cutoff = now - window.total_seconds()

        attempts = self._attempts[key]
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()

        if len(attempts) >= limit:
            # Seconds until the oldest attempt leaves the window.
            retry_after = int(attempts[0] - cutoff) + 1
            return RateLimitDecision(allowed=False, retry_after_seconds=max(retry_after, 1))

        attempts.append(now)
        self._forget_oldest_if_full()
        return RateLimitDecision(allowed=True)

    def _forget_oldest_if_full(self) -> None:
        """Drop the oldest keys when there are too many, bounding memory."""
        while len(self._attempts) > self._max_keys:
            self._attempts.pop(next(iter(self._attempts)))
