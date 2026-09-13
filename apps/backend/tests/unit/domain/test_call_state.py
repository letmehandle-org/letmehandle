"""Every move a call can make and every move it cannot, over all pairs."""

from __future__ import annotations

import itertools

import pytest

from letmehandle.domain.errors import IllegalTransitionError
from letmehandle.domain.models.call_state import (
    ALLOWED,
    TERMINAL,
    CallState,
    can_move,
    is_terminal,
    move,
)

EVERY_STATE = list(CallState)
EVERY_PAIR = list(itertools.product(EVERY_STATE, repeat=2))


def test_every_state_declares_its_transitions() -> None:
    assert set(ALLOWED) == set(CallState)


@pytest.mark.parametrize(("current", "requested"), EVERY_PAIR)
def test_every_pair_either_moves_or_raises_naming_both(
    current: CallState, requested: CallState
) -> None:
    if can_move(current, requested):
        assert move(current, requested) is requested
        return

    with pytest.raises(IllegalTransitionError) as failure:
        move(current, requested)
    assert failure.value.current is current
    assert failure.value.requested is requested


@pytest.mark.parametrize("state", sorted(TERMINAL))
@pytest.mark.parametrize("requested", EVERY_STATE)
def test_a_finished_call_goes_nowhere(state: CallState, requested: CallState) -> None:
    assert not can_move(state, requested)
    with pytest.raises(IllegalTransitionError):
        move(state, requested)


@pytest.mark.parametrize("state", EVERY_STATE)
def test_terminal_states_are_exactly_the_ones_with_nowhere_to_go(state: CallState) -> None:
    assert is_terminal(state) == (ALLOWED[state] == frozenset())


@pytest.mark.parametrize("state", [s for s in EVERY_STATE if s not in TERMINAL])
def test_anything_unfinished_can_fail(state: CallState) -> None:
    assert can_move(state, CallState.FAILED)


@pytest.mark.parametrize("state", EVERY_STATE)
def test_no_state_can_move_to_itself(state: CallState) -> None:
    assert not can_move(state, state)


def test_the_routing_decision_cannot_reach_escalation() -> None:
    assert not can_move(CallState.ROUTING, CallState.ESCALATION_REQUESTED)
    assert not can_move(CallState.ROUTING, CallState.HUMAN_RINGING)


def test_an_unanswered_escalation_returns_the_call_to_the_agent() -> None:
    assert can_move(CallState.HUMAN_RINGING, CallState.AGENT_HANDLING)
    assert can_move(CallState.ESCALATION_REQUESTED, CallState.AGENT_HANDLING)


def test_a_call_can_end_from_every_stage_a_caller_can_hang_up_in() -> None:
    for state in (
        CallState.PASSTHROUGH,
        CallState.AGENT_HANDLING,
        CallState.ESCALATION_REQUESTED,
        CallState.HUMAN_RINGING,
        CallState.HUMAN_JOINED,
    ):
        assert can_move(state, CallState.COMPLETED)


def test_the_transition_table_cannot_be_edited_at_runtime() -> None:
    with pytest.raises(TypeError):
        ALLOWED[CallState.COMPLETED] = frozenset({CallState.RECEIVED})  # type: ignore[index]
