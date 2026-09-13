"""A rate limiter that counts in this process.

Enough for a single-process deployment and for tests. A deployment running several processes
needs a shared one, which is an adapter rather than a change anywhere above this.

The limitation is stated rather than hidden: `is_shared` is false, so whoever scales out can
see that their limits would quietly become per process.
"""

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
        # Bounded, because the keys come from outside: a phone number or an address an attacker
        # chooses. Unbounded, this is a way to exhaust the process's memory by making requests
        # that are all refused.
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
            # How long until the oldest attempt leaves the window. Telling the caller "later"
            # without saying when means they retry immediately and make it worse.
            retry_after = int(attempts[0] - cutoff) + 1
            return RateLimitDecision(allowed=False, retry_after_seconds=max(retry_after, 1))

        attempts.append(now)
        self._forget_oldest_if_full()
        return RateLimitDecision(allowed=True)

    def _forget_oldest_if_full(self) -> None:
        """Drop keys when there are too many.

        Dropping loses a count, which briefly lets somebody past a limit. That is the lesser
        failure: the alternative is a process that an attacker can run out of memory.
        """
        while len(self._attempts) > self._max_keys:
            self._attempts.pop(next(iter(self._attempts)))
