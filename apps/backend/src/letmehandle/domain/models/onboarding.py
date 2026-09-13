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

from letmehandle.domain.errors import InvariantError


class OnboardingStep(StrEnum):
    """One thing to ask about.

    One step per group of preferences, because a step is a screen and a screen that asks about
    two unrelated things is one people abandon.
    """

    CALL_HANDLING = "call_handling"
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
# Four, as the design draws them (D-032). Important contacts and personality are preferences
# edited from settings rather than questions somebody must get through before the product works.
ORDER: Final[tuple[OnboardingStep, ...]] = (
    OnboardingStep.CALL_HANDLING,
    OnboardingStep.HOURS,
    OnboardingStep.AUTHORITY,
    OnboardingStep.NOTIFICATIONS,
)

# Steps whose default is safe to keep. Skipping one of these leaves the assistant more cautious
# rather than less, so a user in a hurry loses convenience and never safety.
#
# `CALL_HANDLING` is deliberately absent: there is no safe default for what to do with a call
# from somebody unknown, and guessing on the user's behalf is the one thing this product must
# not do.
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

    @property
    def next_step(self) -> OnboardingStep | None:
        """What to ask next, or nothing when there is no more to ask.

        The first unsettled step in the declared order, so inserting a step later puts it in
        front of everybody who has not reached it and in front of nobody who has passed it.
        """
        return next((step for step in ORDER if step not in self.settled), None)

    @property
    def is_complete(self) -> bool:
        return self.next_step is None

    @property
    def remaining(self) -> tuple[OnboardingStep, ...]:
        return tuple(step for step in ORDER if step not in self.settled)

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
