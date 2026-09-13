"""How far through setup somebody is, held on the server, against steps declared in one order."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from letmehandle.domain.errors import InvariantError, StepNotAskedError


class OnboardingStep(StrEnum):
    """One screen of setup, asking about one group of preferences."""

    CALL_HANDLING = "call_handling"
    CALL_FORWARDING = "call_forwarding"
    HOURS = "hours"
    AUTHORITY = "authority"
    NOTIFICATIONS = "notifications"


# The order steps are asked in, most consequential first (D-032, D-034).
ORDER: Final[tuple[OnboardingStep, ...]] = (
    OnboardingStep.CALL_HANDLING,
    OnboardingStep.CALL_FORWARDING,
    OnboardingStep.HOURS,
    OnboardingStep.AUTHORITY,
    OnboardingStep.NOTIFICATIONS,
)

# Steps whose default is safe to keep; call handling and forwarding have none.
SKIPPABLE: Final[frozenset[OnboardingStep]] = frozenset(
    {
        OnboardingStep.HOURS,
        OnboardingStep.AUTHORITY,
        OnboardingStep.NOTIFICATIONS,
    }
)


@dataclass(frozen=True, slots=True)
class OnboardingProgress:
    """Which steps have been answered and which deliberately skipped, as sets, not a cursor."""

    completed: frozenset[OnboardingStep] = field(default_factory=frozenset)
    skipped: frozenset[OnboardingStep] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        both = self.completed & self.skipped
        if both:
            raise InvariantError(
                f"{', '.join(sorted(both))} is recorded as both answered and skipped, "
                f"so whether the user was asked depends on which is read first"
            )
        unskippable = self.skipped - SKIPPABLE
        if unskippable:
            raise InvariantError(
                f"{', '.join(sorted(unskippable))} cannot be skipped: it has no default that "
                f"is safe to assume on somebody's behalf"
            )

    @property
    def settled(self) -> frozenset[OnboardingStep]:
        """Every step that does not need asking again."""
        return self.completed | self.skipped

    def completing(self, step: OnboardingStep) -> OnboardingProgress:
        """Record a step as answered, moving it out of the skipped steps."""
        return OnboardingProgress(completed=self.completed | {step}, skipped=self.skipped - {step})

    def skipping(self, step: OnboardingStep) -> OnboardingProgress:
        """Record a step as deliberately passed over."""
        if step not in SKIPPABLE:
            raise InvariantError(
                f"{step} cannot be skipped: it has no default that is safe to assume on "
                f"somebody's behalf"
            )
        return OnboardingProgress(completed=self.completed - {step}, skipped=self.skipped | {step})


@dataclass(frozen=True, slots=True)
class OnboardingFlow:
    """Which of the declared steps one deployment asks, from what the deployment can do."""

    # Whether calls reach the assistant only by the user's phone forwarding them.
    calls_are_forwarded: bool

    @property
    def steps(self) -> tuple[OnboardingStep, ...]:
        """The steps asked, in the declared order."""
        return tuple(step for step in ORDER if self.asks(step))

    def asks(self, step: OnboardingStep) -> bool:
        return step is not OnboardingStep.CALL_FORWARDING or self.calls_are_forwarded


@dataclass(frozen=True, slots=True)
class Onboarding:
    """Stored progress read against this deployment's flow, ignoring steps the flow does not ask."""

    flow: OnboardingFlow
    progress: OnboardingProgress

    @property
    def completed(self) -> tuple[OnboardingStep, ...]:
        return tuple(step for step in self.flow.steps if step in self.progress.completed)

    @property
    def skipped(self) -> tuple[OnboardingStep, ...]:
        return tuple(step for step in self.flow.steps if step in self.progress.skipped)

    @property
    def remaining(self) -> tuple[OnboardingStep, ...]:
        return tuple(step for step in self.flow.steps if step not in self.progress.settled)

    @property
    def next_step(self) -> OnboardingStep | None:
        """The first unsettled step in the declared order, or None when nothing is left to ask."""
        return next(iter(self.remaining), None)

    @property
    def is_complete(self) -> bool:
        return self.next_step is None

    def completing(self, step: OnboardingStep) -> Onboarding:
        """Record a step this deployment asks as answered."""
        return Onboarding(flow=self.flow, progress=self._asked(step).completing(step))

    def skipping(self, step: OnboardingStep) -> Onboarding:
        """Record a step this deployment asks as deliberately passed over."""
        return Onboarding(flow=self.flow, progress=self._asked(step).skipping(step))

    def _asked(self, step: OnboardingStep) -> OnboardingProgress:
        if not self.flow.asks(step):
            raise StepNotAskedError(step)
        return self.progress
