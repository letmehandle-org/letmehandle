"""Counting attempts, and refusing when there have been too many."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.adapters.rate_limit.in_memory import InMemoryRateLimiter
from tests.contracts.fakes import FixedClock

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def limiter(clock: FixedClock) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(clock)


async def test_attempts_within_the_limit_are_allowed(limiter: InMemoryRateLimiter) -> None:
    for _ in range(3):
        assert (await limiter.check("a-key", limit=3, window=WINDOW)).allowed


async def test_the_attempt_after_the_limit_is_refused(limiter: InMemoryRateLimiter) -> None:
    for _ in range(3):
        await limiter.check("a-key", limit=3, window=WINDOW)

    decision = await limiter.check("a-key", limit=3, window=WINDOW)
    assert not decision.allowed
    # Told when to come back. A client told only "no" retries immediately and makes it worse.
    assert 0 < decision.retry_after_seconds <= WINDOW.total_seconds() + 1


async def test_keys_are_counted_separately(limiter: InMemoryRateLimiter) -> None:
    await limiter.check("one", limit=1, window=WINDOW)
    assert (await limiter.check("another", limit=1, window=WINDOW)).allowed


async def test_the_window_slides(limiter: InMemoryRateLimiter, clock: FixedClock) -> None:
    await limiter.check("a-key", limit=1, window=WINDOW)
    assert not (await limiter.check("a-key", limit=1, window=WINDOW)).allowed

    clock.advance(WINDOW.total_seconds() + 1)

    assert (await limiter.check("a-key", limit=1, window=WINDOW)).allowed


async def test_a_refused_attempt_is_not_counted_again(
    limiter: InMemoryRateLimiter, clock: FixedClock
) -> None:
    # Otherwise an attacker holding the limit open indefinitely would extend their own
    # punishment for free, and a legitimate user would never get back in.
    await limiter.check("a-key", limit=1, window=WINDOW)
    for _ in range(5):
        await limiter.check("a-key", limit=1, window=WINDOW)

    clock.advance(WINDOW.total_seconds() + 1)

    assert (await limiter.check("a-key", limit=1, window=WINDOW)).allowed


async def test_it_admits_that_it_is_not_shared(limiter: InMemoryRateLimiter) -> None:
    # An operator running several processes needs to know their limits have quietly become
    # per process.
    assert not limiter.is_shared


async def test_it_forgets_old_keys_rather_than_growing_without_bound(
    clock: FixedClock,
) -> None:
    # The keys come from outside: a number or an address an attacker chooses. Unbounded, this
    # is a way to exhaust the process's memory with requests that are all being refused.
    limiter = InMemoryRateLimiter(clock, max_keys=10)
    for number in range(50):
        await limiter.check(f"key-{number}", limit=5, window=WINDOW)

    assert len(limiter._attempts) <= 10
