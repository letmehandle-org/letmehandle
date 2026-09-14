"""Trying an operation again, only when it is safe to repeat and its failure is retryable."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.failures import classify

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many attempts, with jittered waits growing from `first_wait` to `longest_wait`."""

    attempts: int = 3
    first_wait: timedelta = timedelta(milliseconds=100)
    longest_wait: timedelta = timedelta(seconds=1)

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise InvariantError("a policy with no attempts never runs the operation")
        if self.first_wait <= timedelta(0) or self.longest_wait < self.first_wait:
            raise InvariantError("waits grow from a positive first wait to a longest one")

    def ceiling(self, after_attempt: int) -> float:
        """The longest wait, in seconds, after the `after_attempt`-th attempt failed."""
        grown = self.first_wait.total_seconds() * 2.0 ** (after_attempt - 1)
        return min(grown, self.longest_wait.total_seconds())


async def retry_idempotent[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run `operation`, safe to repeat, again after each retryable failure, up to `attempts`."""
    attempt = 1
    while True:
        try:
            return await operation()
        except Exception as error:
            if attempt >= policy.attempts or not classify(error).retryable:
                raise
        # Jittered across the whole range up to the ceiling.
        await sleep(jitter() * policy.ceiling(attempt))
        attempt += 1
