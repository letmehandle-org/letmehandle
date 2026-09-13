"""How far through setting up somebody is.

The two failures this guards against: asking somebody a question they have already answered,
and assuming an answer on their behalf for a question that has no safe default. A third arrives
with the steps only some deployments ask: asking a question whose answer changes nothing here.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError, StepNotAskedError
from letmehandle.domain.models.onboarding import (
    ORDER,
    SKIPPABLE,
    Onboarding,
    OnboardingFlow,
    OnboardingProgress,
    OnboardingStep,
)

FORWARDED = OnboardingFlow(calls_are_forwarded=True)
NOT_FORWARDED = OnboardingFlow(calls_are_forwarded=False)


def onboarding(
    flow: OnboardingFlow = FORWARDED, **progress: frozenset[OnboardingStep]
) -> Onboarding:
    return Onboarding(flow=flow, progress=OnboardingProgress(**progress))


class TestTheOrder:
    def test_every_step_appears_exactly_once(self) -> None:
        # A step missing from the order can never be reached; a step listed twice is asked
        # twice. Both are silent.
        assert sorted(ORDER) == sorted(OnboardingStep)
        assert len(ORDER) == len(set(ORDER))

    @pytest.mark.parametrize("step", [OnboardingStep.CALL_HANDLING, OnboardingStep.CALL_FORWARDING])
    def test_what_calls_depend_on_cannot_be_skipped(self, step: OnboardingStep) -> None:
        # Call handling has no safe default for an unknown caller. Forwarding has no default at
        # all: without it no call reaches the assistant, and setup finishing says it works.
        assert step not in SKIPPABLE

    def test_setup_asks_forwarding_right_after_call_handling(self) -> None:
        # D-032 and D-034. Where calls go is settled before the hours they are handled in.
        assert ORDER == (
            OnboardingStep.CALL_HANDLING,
            OnboardingStep.CALL_FORWARDING,
            OnboardingStep.HOURS,
            OnboardingStep.AUTHORITY,
            OnboardingStep.NOTIFICATIONS,
        )

    def test_everything_skippable_is_a_real_step(self) -> None:
        assert set(ORDER) >= SKIPPABLE


class TestTheFlow:
    def test_a_forwarded_deployment_asks_every_step(self) -> None:
        assert FORWARDED.steps == ORDER

    def test_elsewhere_forwarding_is_not_asked_and_the_order_is_kept(self) -> None:
        assert NOT_FORWARDED.steps == tuple(
            step for step in ORDER if step is not OnboardingStep.CALL_FORWARDING
        )


class TestProgress:
    def test_somebody_who_has_never_started_is_at_the_first_step(self) -> None:
        state = onboarding()
        assert state.next_step is ORDER[0]
        assert not state.is_complete
        assert state.remaining == ORDER

    def test_answering_moves_on(self) -> None:
        state = onboarding().completing(OnboardingStep.CALL_HANDLING)
        assert state.next_step is OnboardingStep.CALL_FORWARDING

    def test_without_forwarding_call_handling_leads_to_hours(self) -> None:
        state = onboarding(NOT_FORWARDED).completing(OnboardingStep.CALL_HANDLING)
        assert state.next_step is OnboardingStep.HOURS

    def test_skipping_moves_on_too(self) -> None:
        state = (
            onboarding()
            .completing(OnboardingStep.CALL_HANDLING)
            .completing(OnboardingStep.CALL_FORWARDING)
            .skipping(OnboardingStep.HOURS)
        )
        assert state.next_step is OnboardingStep.AUTHORITY

    def test_a_settled_step_is_never_asked_again(self) -> None:
        state = onboarding().completing(OnboardingStep.HOURS)
        assert OnboardingStep.HOURS not in state.remaining

    def test_answering_a_skipped_step_records_it_as_answered(self) -> None:
        # Somebody who came back to fill in what they skipped has answered it, and the record
        # should say so rather than leaving them looking like they never did.
        state = onboarding().skipping(OnboardingStep.HOURS).completing(OnboardingStep.HOURS)

        assert state.completed == (OnboardingStep.HOURS,)
        assert state.skipped == ()

    def test_skipping_a_step_that_was_answered_records_it_as_skipped(self) -> None:
        state = onboarding().completing(OnboardingStep.HOURS).skipping(OnboardingStep.HOURS)

        assert state.skipped == (OnboardingStep.HOURS,)
        assert state.completed == ()

    @pytest.mark.parametrize("flow", [FORWARDED, NOT_FORWARDED])
    def test_finishing_every_step_asked_completes_it(self, flow: OnboardingFlow) -> None:
        state = Onboarding(flow=flow, progress=OnboardingProgress())
        for step in flow.steps:
            state = state.completing(step)

        assert state.is_complete
        assert state.next_step is None
        assert state.remaining == ()

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
            onboarding().skipping(step)

    def test_progress_is_immutable(self) -> None:
        progress = OnboardingProgress()
        with pytest.raises(AttributeError):
            progress.completed = frozenset(OnboardingStep)  # type: ignore[misc]

    def test_recording_returns_a_new_value(self) -> None:
        original = onboarding()
        original.completing(OnboardingStep.CALL_HANDLING)
        assert original.progress.completed == frozenset()

    def test_inserting_a_step_puts_it_in_front_of_whoever_has_not_reached_it(self) -> None:
        # The reason progress is a set rather than a cursor: a cursor silently means something
        # else the moment the order changes.
        partway = onboarding(
            completed=frozenset({OnboardingStep.CALL_HANDLING, OnboardingStep.HOURS})
        )
        assert partway.next_step is OnboardingStep.CALL_FORWARDING


class TestAStepADeploymentDoesNotAsk:
    def test_recording_it_is_refused_naming_the_step(self) -> None:
        with pytest.raises(StepNotAskedError) as refused:
            onboarding(NOT_FORWARDED).completing(OnboardingStep.CALL_FORWARDING)
        assert refused.value.step == OnboardingStep.CALL_FORWARDING

    def test_skipping_it_is_refused_the_same_way(self) -> None:
        with pytest.raises(StepNotAskedError):
            onboarding(NOT_FORWARDED).skipping(OnboardingStep.CALL_FORWARDING)

    def test_an_answer_recorded_where_it_was_asked_is_ignored_where_it_is_not(self) -> None:
        # A deployment can stop needing forwarding. The stored row stays valid, and the answer
        # neither shows as done nor counts towards being done.
        state = onboarding(
            NOT_FORWARDED,
            completed=frozenset({OnboardingStep.CALL_HANDLING, OnboardingStep.CALL_FORWARDING}),
        )
        assert state.completed == (OnboardingStep.CALL_HANDLING,)
        assert state.next_step is OnboardingStep.HOURS

    def test_a_deployment_that_starts_forwarding_asks_somebody_who_had_finished(self) -> None:
        # Finished meant finished for the calls that deployment carried. Without forwarding set
        # up, none arrive on this one, so setup is not done until it is.
        every_other_step = frozenset(NOT_FORWARDED.steps)
        state = onboarding(FORWARDED, completed=every_other_step)
        assert not state.is_complete
        assert state.next_step is OnboardingStep.CALL_FORWARDING
