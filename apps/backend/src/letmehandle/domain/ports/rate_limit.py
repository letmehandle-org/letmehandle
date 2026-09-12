"""Limiting how often something may be attempted.

A port because the right store depends on the deployment: one process can hold counters in
memory, several need somewhere shared. The interface is the same either way, and nothing above
it learns which it got.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Whether an attempt may proceed, and when the next one may.

    `retry_after_seconds` is returned rather than left for the caller to guess, because a
    client told only "no" will retry immediately and make the problem worse.
    """

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(ABC):
    """Counts attempts against a key."""

    @abstractmethod
    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        """Record an attempt and say whether it is within the limit.

        Recording and deciding are one operation on purpose. Two calls — ask, then record —
        leave a gap in which several requests all pass, which is exactly the burst a limit
        exists to stop.
        """
