"""Trying an operation again, where that is correct, and nowhere else.

Two things must both be true. The failure must be one that trying again can fix — a timeout, a
dependency that could not be reached — which the failure taxonomy decides, never the call site.
And the operation must be safe to repeat: storing a whole call again, or ending a call that may
already have ended, leaves the world as one attempt would. Dialling somebody again does not; it
rings their phone twice. The second is a property of the operation and cannot be read off an
exception, so it is stated in the name of the one function that retries, and a call site that
reaches for it is claiming it.

The waits between attempts grow and are jittered across their whole range, so that every call
that met the same outage does not come back to the same dependency at the same instant.
"""

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
    """How many attempts, and how long the waits between them may grow to.

    The wait before attempt `n + 1` is drawn evenly from nothing up to `first_wait` doubled `n - 1`
    times, and never more than `longest_wait`.
    """

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
    """Run `operation`, again after a retryable failure, until it succeeds or attempts run out.

    Only for an operation that is safe to repeat: see the module's account of why that cannot be
    checked here. A failure that is not retryable is raised at once, and the last failure is raised
    when attempts run out. Cancellation is never retried.
    """
    attempt = 1
    while True:
        try:
            return await operation()
        except Exception as error:
            if attempt >= policy.attempts or not classify(error).retryable:
                raise
        # Anywhere from nothing to the ceiling, so retries after one outage do not arrive together.
        await sleep(jitter() * policy.ceiling(attempt))
        attempt += 1
