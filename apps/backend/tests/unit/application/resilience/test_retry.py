"""Trying again happens only after a failure that trying again can fix, and within bounds."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from letmehandle.application.resilience.retry import RetryPolicy, retry_idempotent
from letmehandle.domain.errors import (
    AlreadyRecordedError,
    InvariantError,
    ProviderError,
    StorageUnavailableError,
)

POLICY = RetryPolicy(
    attempts=4, first_wait=timedelta(milliseconds=100), longest_wait=timedelta(milliseconds=250)
)


class Flaky:
    """An operation that fails with each of `failures` in turn, then succeeds."""

    def __init__(self, *failures: Exception) -> None:
        self._failures = list(failures)
        self.attempts = 0

    async def __call__(self) -> str:
        self.attempts += 1
        if self._failures:
            raise self._failures.pop(0)
        return "stored"


class Waits:
    """The waits asked for, instead of waiting."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


async def test_a_retryable_failure_is_tried_again_until_it_succeeds() -> None:
    operation = Flaky(TimeoutError(), StorageUnavailableError())
    waits = Waits()

    result = await retry_idempotent(operation, policy=POLICY, sleep=waits, jitter=lambda: 1.0)

    assert result == "stored"
    assert operation.attempts == 3
    # Doubling from the first wait, full jitter at its top here.
    assert waits.seconds == [0.1, 0.2]


@pytest.mark.parametrize(
    "failure",
    [
        ProviderError("telephony", "that call does not exist", retryable=False),
        AlreadyRecordedError("summary", "a-call"),
        RuntimeError("a defect"),
    ],
    ids=["refused", "conflict", "defect"],
)
async def test_a_failure_trying_again_cannot_fix_is_raised_at_once(failure: Exception) -> None:
    operation = Flaky(failure)
    waits = Waits()

    with pytest.raises(type(failure)):
        await retry_idempotent(operation, policy=POLICY, sleep=waits)

    assert operation.attempts == 1
    assert waits.seconds == []


async def test_the_last_failure_is_raised_when_attempts_run_out_and_waits_stop_growing() -> None:
    operation = Flaky(*(TimeoutError() for _ in range(5)))
    waits = Waits()

    with pytest.raises(TimeoutError):
        await retry_idempotent(operation, policy=POLICY, sleep=waits, jitter=lambda: 1.0)

    assert operation.attempts == 4
    assert waits.seconds == [0.1, 0.2, 0.25]


async def test_jitter_spreads_each_wait_across_its_whole_range() -> None:
    waits = Waits()

    await retry_idempotent(
        Flaky(TimeoutError(), TimeoutError()), policy=POLICY, sleep=waits, jitter=lambda: 0.5
    )

    assert waits.seconds == [0.05, 0.1]


async def test_cancellation_is_never_retried() -> None:
    attempts = 0

    async def cancelled() -> None:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await retry_idempotent(cancelled, policy=POLICY, sleep=Waits())

    assert attempts == 1


async def test_by_default_it_really_waits_between_attempts() -> None:
    policy = RetryPolicy(attempts=2, first_wait=timedelta(milliseconds=1))

    assert await retry_idempotent(Flaky(TimeoutError()), policy=policy) == "stored"


@pytest.mark.parametrize(
    "arguments",
    [
        {"attempts": 0},
        {"first_wait": timedelta(0)},
        {"first_wait": timedelta(seconds=2), "longest_wait": timedelta(seconds=1)},
    ],
)
def test_a_policy_that_could_never_work_is_refused(arguments: dict[str, object]) -> None:
    with pytest.raises(InvariantError):
        RetryPolicy(**arguments)  # type: ignore[arg-type]  # each value is of its field's type
