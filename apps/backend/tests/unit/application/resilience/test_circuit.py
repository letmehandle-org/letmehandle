"""A circuit opens on a failing dependency, lets one trial through after a cool-off, and closes."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from letmehandle.application.resilience.circuit import (
    CIRCUIT_TRANSITION,
    CircuitBreaker,
    CircuitOpenError,
    CircuitPolicy,
    Circuits,
    CircuitState,
    Dependency,
)
from letmehandle.domain.errors import InvariantError, ProviderError
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.ports.notification import DevicePlatform
from tests.support.recording_metrics import RecordingMetrics

POLICY = CircuitPolicy(failures_to_open=3, cool_off=timedelta(seconds=30))


class Moment:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.seconds = 1_000.0

    def __call__(self) -> float:
        return self.seconds


async def succeeding() -> str:
    return "answered"


async def timing_out() -> str:
    raise TimeoutError


async def refusing() -> str:
    raise ProviderError("telephony", "no such call", retryable=False)


async def defective() -> str:
    raise RuntimeError("a mistake of ours")


def breaker(moment: Moment, metrics: RecordingMetrics | None = None) -> CircuitBreaker:
    return CircuitBreaker(
        "speech", policy=POLICY, now=moment, metrics=metrics or RecordingMetrics()
    )


def state_of(circuit: CircuitBreaker) -> CircuitState:
    # Read through a call, so the type checker sees each reading of the clock.
    return circuit.state


async def fail(circuit: CircuitBreaker, times: int) -> None:
    for _ in range(times):
        with pytest.raises(TimeoutError):
            await circuit.call(timing_out)


async def test_a_circuit_opens_after_consecutive_failures_and_refuses_without_asking() -> None:
    metrics = RecordingMetrics()
    circuit = breaker(Moment(), metrics)
    asked = 0

    async def counted() -> str:
        nonlocal asked
        asked += 1
        return "answered"

    await fail(circuit, 3)
    with pytest.raises(CircuitOpenError) as refused:
        await circuit.call(counted)

    assert state_of(circuit) is CircuitState.OPEN
    assert circuit.is_refusing
    assert asked == 0
    assert refused.value.dependency == "speech"
    assert classify(refused.value).kind is FailureKind.CIRCUIT_OPEN
    assert metrics.counted(CIRCUIT_TRANSITION, provider="speech", outcome="open") == 1


async def test_a_success_between_failures_starts_the_count_again() -> None:
    circuit = breaker(Moment())

    await fail(circuit, 2)
    await circuit.call(succeeding)
    await fail(circuit, 2)

    assert state_of(circuit) is CircuitState.CLOSED


async def test_after_the_cool_off_one_trial_is_let_through_and_its_success_closes_it() -> None:
    moment = Moment()
    metrics = RecordingMetrics()
    circuit = breaker(moment, metrics)
    await fail(circuit, 3)
    moment.seconds += 30
    trial_started = asyncio.Event()
    finish = asyncio.Event()

    async def slow_trial() -> str:
        trial_started.set()
        await finish.wait()
        return "answered"

    assert state_of(circuit) is CircuitState.HALF_OPEN
    assert not circuit.is_refusing
    trial = asyncio.create_task(circuit.call(slow_trial))
    await trial_started.wait()
    # A second request while the trial runs is refused.
    assert circuit.is_refusing
    with pytest.raises(CircuitOpenError):
        await circuit.call(succeeding)
    finish.set()

    assert await trial == "answered"
    assert state_of(circuit) is CircuitState.CLOSED
    assert [each.labels["outcome"] for each in metrics.counts] == ["open", "half_open", "closed"]


async def test_a_trial_that_fails_opens_it_for_another_cool_off() -> None:
    moment = Moment()
    circuit = breaker(moment)
    await fail(circuit, 3)
    moment.seconds += 30

    await fail(circuit, 1)

    assert state_of(circuit) is CircuitState.OPEN
    moment.seconds += 29
    assert state_of(circuit) is CircuitState.OPEN
    moment.seconds += 1
    assert state_of(circuit) is CircuitState.HALF_OPEN


async def test_a_dependency_that_answers_no_is_a_dependency_that_is_working() -> None:
    moment = Moment()
    circuit = breaker(moment)
    await fail(circuit, 2)

    for _ in range(5):
        with pytest.raises(ProviderError):
            await circuit.call(refusing)

    assert state_of(circuit) is CircuitState.CLOSED


async def test_a_defect_says_nothing_about_the_dependency() -> None:
    moment = Moment()
    circuit = breaker(moment)
    await fail(circuit, 2)
    with pytest.raises(RuntimeError):
        await circuit.call(defective)
    await fail(circuit, 1)

    assert state_of(circuit) is CircuitState.OPEN


@pytest.mark.parametrize("ending", ["defect", "cancelled"])
async def test_a_trial_that_ends_inconclusively_lets_the_next_one_run(ending: str) -> None:
    moment = Moment()
    circuit = breaker(moment)
    await fail(circuit, 3)
    moment.seconds += 30

    async def cancelled() -> str:
        raise asyncio.CancelledError

    with pytest.raises(RuntimeError if ending == "defect" else asyncio.CancelledError):
        await circuit.call(defective if ending == "defect" else cancelled)

    assert state_of(circuit) is CircuitState.HALF_OPEN
    assert await circuit.call(succeeding) == "answered"
    assert state_of(circuit) is CircuitState.CLOSED


def test_every_dependency_has_its_own_circuit_and_starts_closed() -> None:
    circuits = Circuits(metrics=RecordingMetrics())

    assert circuits.states() == dict.fromkeys(
        ["telephony", "speech", "model", *(f"push_{each}" for each in DevicePlatform)],
        CircuitState.CLOSED,
    )
    assert circuits[Dependency.MODEL] is circuits[Dependency.MODEL]
    assert circuits[Dependency.MODEL] is not circuits[Dependency.SPEECH]
    assert circuits.push(DevicePlatform.IOS) is not circuits.push(DevicePlatform.ANDROID)


async def test_one_dependency_failing_leaves_the_others_closed() -> None:
    circuits = Circuits(metrics=RecordingMetrics(), policy=POLICY, now=Moment())

    await fail(circuits.push(DevicePlatform.IOS), 3)

    states = circuits.states()
    assert states.pop("push_ios") is CircuitState.OPEN
    assert set(states.values()) == {CircuitState.CLOSED}


@pytest.mark.parametrize(
    "arguments", [{"failures_to_open": 0}, {"cool_off": timedelta(0)}], ids=["never", "no time"]
)
def test_a_policy_that_could_never_work_is_refused(arguments: dict[str, object]) -> None:
    with pytest.raises(InvariantError):
        CircuitPolicy(**arguments)  # type: ignore[arg-type]  # each value is of its field's type
