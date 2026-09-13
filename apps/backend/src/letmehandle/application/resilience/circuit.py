"""Circuits that stop asking a failing dependency, letting one trial through after a cool-off."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import DomainError, InvariantError
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.ports.notification import DevicePlatform
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)


class Dependency(StrEnum):
    """What calls depend on, named by role rather than by vendor."""

    TELEPHONY = "telephony"
    SPEECH = "speech"
    MODEL = "model"


class CircuitState(StrEnum):
    """Where a circuit stands."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


CIRCUIT_TRANSITION: Final = catalogue.count(
    "circuit.transition", provider=catalogue.NAMED_IN_CODE, outcome=CircuitState
)


class CircuitOpenError(DomainError):
    """A request was not made, because its dependency's circuit is open."""

    failure_kind = FailureKind.CIRCUIT_OPEN

    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is failing, so it was not asked")
        self.dependency = dependency


@dataclass(frozen=True, slots=True)
class CircuitPolicy:
    """How many failures in a row open a circuit, and how long it stays open before a trial."""

    failures_to_open: int = 5
    cool_off: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        if self.failures_to_open < 1 or self.cool_off <= timedelta(0):
            raise InvariantError("a circuit opens after at least one failure, for some time")


class CircuitBreaker:
    """One dependency's circuit, shared by every call that asks it."""

    def __init__(
        self,
        dependency: str,
        *,
        policy: CircuitPolicy,
        now: Callable[[], float],
        metrics: MetricsRecorder,
    ) -> None:
        self._dependency = dependency
        self._policy = policy
        self._now = now
        self._metrics = metrics
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_running = False

    @property
    def state(self) -> CircuitState:
        """Where the circuit stands now. An open circuit whose cool-off has ended is half open."""
        if self._state is CircuitState.OPEN and self._cooled_off():
            return CircuitState.HALF_OPEN
        return self._state

    @property
    def is_refusing(self) -> bool:
        """Whether a request made now would be refused without being tried."""
        state = self.state
        trial_running = state is CircuitState.HALF_OPEN and self._trial_running
        return state is CircuitState.OPEN or trial_running

    async def call[T](
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        failed_when: Callable[[T], bool] | None = None,
    ) -> T:
        """Run `operation` if admitted and count how it went; `CircuitOpenError` when refused."""
        if not self._admit():
            raise CircuitOpenError(self._dependency)
        try:
            result = await operation()
        except asyncio.CancelledError:
            self._inconclusive()
            raise
        except Exception as error:
            failure = classify(error)
            if failure.retryable:
                self._failed()
            elif failure.is_defect:
                self._inconclusive()
            else:
                self._succeeded()
            raise
        if failed_when is not None and failed_when(result):
            self._failed()
        else:
            self._succeeded()
        return result

    def _admit(self) -> bool:
        match self.state:
            case CircuitState.CLOSED:
                return True
            case CircuitState.OPEN:
                return False
            case CircuitState.HALF_OPEN if not self._trial_running:
                self._move(CircuitState.HALF_OPEN)
                self._trial_running = True
                return True
            case _:
                return False

    def _succeeded(self) -> None:
        self._failures = 0
        self._trial_running = False
        if self._state is not CircuitState.CLOSED:
            self._move(CircuitState.CLOSED)

    def _failed(self) -> None:
        self._trial_running = False
        self._failures += 1
        if self._state is CircuitState.HALF_OPEN or self._failures >= self._policy.failures_to_open:
            self._opened_at = self._now()
            self._move(CircuitState.OPEN)

    def _inconclusive(self) -> None:
        # A trial that ended without saying anything about the dependency lets the next one run.
        self._trial_running = False

    def _cooled_off(self) -> bool:
        return self._now() - self._opened_at >= self._policy.cool_off.total_seconds()

    def _move(self, state: CircuitState) -> None:
        if state is self._state:
            return
        self._state = state
        self._metrics.increment(
            CIRCUIT_TRANSITION, {"provider": self._dependency, "outcome": state.value}
        )
        if state is CircuitState.OPEN:
            logger.error("circuit.opened", dependency=self._dependency)
        else:
            logger.info("circuit.moved", dependency=self._dependency, state=state.value)


class Circuits:
    """One circuit per dependency and per push platform, for the life of the process."""

    def __init__(
        self,
        *,
        metrics: MetricsRecorder,
        policy: CircuitPolicy | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._metrics = metrics
        self._policy = policy or CircuitPolicy()
        self._now = now
        self._breakers: dict[str, CircuitBreaker] = {}
        for dependency in Dependency:
            self._breaker(dependency.value)
        for platform in DevicePlatform:
            self._breaker(_push(platform))

    def __getitem__(self, dependency: Dependency) -> CircuitBreaker:
        return self._breakers[dependency.value]

    def push(self, platform: DevicePlatform) -> CircuitBreaker:
        """The circuit of the push service that delivers to `platform`."""
        return self._breakers[_push(platform)]

    def states(self) -> dict[str, CircuitState]:
        """Where every circuit stands, by dependency, for readiness and diagnostics."""
        return {name: breaker.state for name, breaker in self._breakers.items()}

    def _breaker(self, name: str) -> None:
        self._breakers[name] = CircuitBreaker(
            name, policy=self._policy, now=self._now, metrics=self._metrics
        )


def _push(platform: DevicePlatform) -> str:
    return f"push_{platform.value}"
