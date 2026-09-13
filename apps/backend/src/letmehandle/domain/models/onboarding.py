"""How far through setting up somebody is.

Held on the server rather than on the device, so that reinstalling, changing phone or signing
in somewhere else resumes where they were instead of starting again. Somebody who has answered
nine questions should never be asked them a second time because their phone broke.

The order is declared once. A step's position is not something each screen decides for itself,
because then two screens disagree about what comes next and the flow loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from letmehandle.domain.errors import InvariantError, StepNotAskedError


class OnboardingStep(StrEnum):
    """One thing to ask about.

    One step per group of preferences, because a step is a screen and a screen that asks about
    two unrelated things is one people abandon.
    """

    CALL_HANDLING = "call_handling"
    CALL_FORWARDING = "call_forwarding"
    HOURS = "hours"
    AUTHORITY = "authority"
    NOTIFICATIONS = "notifications"


# The order they are asked in, declared once.
#
# Call handling comes first because it is the only one with no safe default: until the user has
# said what should happen to an unknown caller, the assistant cannot do anything at all. The
# rest descend by how much the answer changes, so somebody who stops halfway has still answered
# the questions that mattered most.
#
# Forwarding follows it, on the deployments that ask it at all (D-034): where calls arrive only
# by being forwarded, nothing after it matters until they do.
#
# The four the design draws (D-032), and that one. Important contacts and personality are
# preferences edited from settings rather than questions somebody must get through first.
ORDER: Final[tuple[OnboardingStep, ...]] = (
    OnboardingStep.CALL_HANDLING,
    OnboardingStep.CALL_FORWARDING,
    OnboardingStep.HOURS,
    OnboardingStep.AUTHORITY,
    OnboardingStep.NOTIFICATIONS,
)

# Steps whose default is safe to keep. Skipping one of these leaves the assistant more cautious
# rather than less, so a user in a hurry loses convenience and never safety.
#
# `CALL_HANDLING` is deliberately absent: there is no safe default for what to do with a call
# from somebody unknown, and guessing on the user's behalf is the one thing this product must
# not do. `CALL_FORWARDING` is absent for a plainer reason: skipped, no call ever arrives, and a
# setup that finishes anyway tells somebody the product works when it cannot.
SKIPPABLE: Final[frozenset[OnboardingStep]] = frozenset(
    {
        OnboardingStep.HOURS,
        OnboardingStep.AUTHORITY,
        OnboardingStep.NOTIFICATIONS,
    }
)


@dataclass(frozen=True, slots=True)
class OnboardingProgress:
    """Which steps have been answered or deliberately skipped.

    A set rather than a cursor. A cursor says where somebody is and forgets how they got there,
    so it cannot tell a step that was answered from one that was skipped — and it breaks the
    moment a step is inserted, because everybody's position silently means something else.
    """

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
        """Record a step as answered.

        Answering a step that was skipped moves it: somebody who came back to fill in what they
        skipped has answered it, and the record should say so.
        """
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
    """Which of the declared steps one deployment asks.

    Built from what the deployment can do rather than from which deployment it is, so the order
    stays declared once above and a step a deployment does not ask is simply absent from it.
    """

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
    """Somebody's progress, read against the steps this deployment asks.

    The progress is stored; the flow is not, because it belongs to the deployment. A step
    recorded where it was asked and read where it is not is ignored rather than refused (as
    D-032 does for a removed step), so the same row stays valid wherever it is read.
    """

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
        """What to ask next, or nothing when there is no more to ask.

        The first unsettled step in the declared order, so inserting a step later puts it in
        front of everybody who has not reached it and in front of nobody who has passed it.
        """
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
