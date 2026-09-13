"""Limiting how often something may be attempted, in memory or in a shared store."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Whether an attempt may proceed, and how many seconds until the next one may."""

    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(ABC):
    """Counts attempts against a key."""

    @property
    @abstractmethod
    def is_shared(self) -> bool:
        """Whether every process counts against the same store, as readiness reports."""

    @abstractmethod
    async def check(self, key: str, *, limit: int, window: timedelta) -> RateLimitDecision:
        """Record an attempt and say whether it is within the limit, as one atomic operation."""
