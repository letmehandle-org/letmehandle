"""How far through setting up somebody is.

The two failures this guards against: asking somebody a question they have already answered,
and assuming an answer on their behalf for the one question that has no safe default.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.onboarding import (
    ORDER,
    SKIPPABLE,
    OnboardingProgress,
    OnboardingStep,
)


class TestTheOrder:
    def test_every_step_appears_exactly_once(self) -> None:
        # A step missing from the order can never be reached; a step listed twice is asked
        # twice. Both are silent.
        assert sorted(ORDER) == sorted(OnboardingStep)
        assert len(ORDER) == len(set(ORDER))

    def test_call_handling_cannot_be_skipped(self) -> None:
        # There is no safe default for what to do with a call from somebody unknown, and
        # guessing on the user's behalf is the one thing this product must not do.
        assert OnboardingStep.CALL_HANDLING not in SKIPPABLE

    def test_the_introduction_cannot_be_skipped_either(self) -> None:
        # Not because it is dangerous, but because skipping an explanation nobody read is how
        # somebody ends up not knowing what the product does.
        assert OnboardingStep.INTRODUCTION not in SKIPPABLE

    def test_everything_skippable_is_a_real_step(self) -> None:
        assert set(ORDER) >= SKIPPABLE


class TestProgress:
    def test_somebody_who_has_never_started_is_at_the_first_step(self) -> None:
        progress = OnboardingProgress()
        assert progress.next_step is ORDER[0]
        assert not progress.is_complete
        assert progress.remaining == ORDER

    def test_answering_moves_on(self) -> None:
        progress = OnboardingProgress().completing(OnboardingStep.INTRODUCTION)
        assert progress.next_step is OnboardingStep.CALL_HANDLING

    def test_skipping_moves_on_too(self) -> None:
        progress = (
            OnboardingProgress()
            .completing(OnboardingStep.INTRODUCTION)
            .completing(OnboardingStep.CALL_HANDLING)
            .skipping(OnboardingStep.IMPORTANT_CONTACTS)
        )
        assert progress.next_step is OnboardingStep.HOURS

    def test_a_settled_step_is_never_asked_again(self) -> None:
        progress = OnboardingProgress().completing(OnboardingStep.HOURS)
        assert OnboardingStep.HOURS not in progress.remaining

    def test_answering_a_skipped_step_records_it_as_answered(self) -> None:
        # Somebody who came back to fill in what they skipped has answered it, and the record
        # should say so rather than leaving them looking like they never did.
        progress = OnboardingProgress().skipping(OnboardingStep.HOURS)
        progress = progress.completing(OnboardingStep.HOURS)

        assert OnboardingStep.HOURS in progress.completed
        assert OnboardingStep.HOURS not in progress.skipped

    def test_skipping_a_step_that_was_answered_records_it_as_skipped(self) -> None:
        progress = OnboardingProgress().completing(OnboardingStep.HOURS)
        progress = progress.skipping(OnboardingStep.HOURS)

        assert OnboardingStep.HOURS in progress.skipped
        assert OnboardingStep.HOURS not in progress.completed

    def test_finishing_everything_completes_it(self) -> None:
        progress = OnboardingProgress()
        for step in ORDER:
            progress = progress.completing(step)

        assert progress.is_complete
        assert progress.next_step is None
        assert progress.remaining == ()

    def test_a_step_cannot_be_both_answered_and_skipped(self) -> None:
        # Whether the user was asked would depend on which set is read first.
        with pytest.raises(InvariantError, match="both answered and skipped"):
            OnboardingProgress(
                completed=frozenset({OnboardingStep.HOURS}),
                skipped=frozenset({OnboardingStep.HOURS}),
            )

    @pytest.mark.parametrize("step", [step for step in ORDER if step not in SKIPPABLE])
    def test_an_unskippable_step_cannot_be_recorded_as_skipped(self, step: OnboardingStep) -> None:
        with pytest.raises(InvariantError, match="cannot be skipped"):
            OnboardingProgress(skipped=frozenset({step}))

    @pytest.mark.parametrize("step", [step for step in ORDER if step not in SKIPPABLE])
    def test_skipping_an_unskippable_step_is_refused(self, step: OnboardingStep) -> None:
        with pytest.raises(InvariantError, match="cannot be skipped"):
            OnboardingProgress().skipping(step)

    def test_progress_is_immutable(self) -> None:
        progress = OnboardingProgress()
        with pytest.raises(AttributeError):
            progress.completed = frozenset(OnboardingStep)  # type: ignore[misc]

    def test_recording_returns_a_new_value(self) -> None:
        original = OnboardingProgress()
        original.completing(OnboardingStep.INTRODUCTION)
        assert original.completed == frozenset()

    def test_inserting_a_step_puts_it_in_front_of_whoever_has_not_reached_it(self) -> None:
        # The reason progress is a set rather than a cursor: a cursor silently means something
        # else the moment the order changes.
        partway = OnboardingProgress(
            completed=frozenset({OnboardingStep.INTRODUCTION, OnboardingStep.CALL_HANDLING})
        )
        assert partway.next_step is ORDER[2]
