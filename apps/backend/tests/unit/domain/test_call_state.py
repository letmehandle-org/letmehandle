"""Every move a call can make, and every move it cannot.

Driven by the full cartesian product rather than a list of interesting cases. A hand-written
list covers what its author thought of; the product covers what they did not, and it is the
transitions nobody considered that produce a call stuck in a state with no way out.
"""

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
    # The guard that makes adding a state a decision rather than an oversight: a new member
    # with no entry here fails, instead of silently becoming a state calls cannot leave.
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
    # Any provider, at any moment, can leave a call unrecoverable. A state that cannot reach
    # FAILED is a state where that call has to be left running.
    assert can_move(state, CallState.FAILED)


@pytest.mark.parametrize("state", EVERY_STATE)
def test_no_state_can_move_to_itself(state: CallState) -> None:
    # A self-transition is how a duplicate provider callback becomes a second, real state
    # change. Idempotency is the orchestrator's job, but the state machine must not make the
    # mistake available in the first place.
    assert not can_move(state, state)


def test_the_routing_decision_cannot_reach_escalation() -> None:
    # Escalation means the agent, having spoken to the caller, wants a human. There is nothing
    # to escalate about before the call has been handled.
    assert not can_move(CallState.ROUTING, CallState.ESCALATION_REQUESTED)
    assert not can_move(CallState.ROUTING, CallState.HUMAN_RINGING)


def test_an_unanswered_escalation_returns_the_call_to_the_agent() -> None:
    # The failure this product exists to prevent is a call that dies because its owner was
    # busy. Both escalation states must be able to hand the call back.
    assert can_move(CallState.HUMAN_RINGING, CallState.AGENT_HANDLING)
    assert can_move(CallState.ESCALATION_REQUESTED, CallState.AGENT_HANDLING)


def test_a_call_can_end_from_every_stage_a_caller_can_hang_up_in() -> None:
    # A caller hangs up when they like. Every state where someone is on the line must be able
    # to reach a completed call rather than only a failed one.
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
