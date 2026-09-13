"""Where a call can be, and every move it may make from there, in one table."""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from letmehandle.domain.errors import IllegalTransitionError


class CallState(StrEnum):
    """The life of a call, with rejected, completed and failed as three distinct endings."""

    RECEIVED = "received"
    ROUTING = "routing"
    PASSTHROUGH = "passthrough"
    AGENT_HANDLING = "agent_handling"
    ESCALATION_REQUESTED = "escalation_requested"
    HUMAN_RINGING = "human_ringing"
    HUMAN_JOINED = "human_joined"
    REJECTED = "rejected"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL: Final[frozenset[CallState]] = frozenset(
    {CallState.REJECTED, CallState.COMPLETED, CallState.FAILED}
)

# Where each state may move, before FAILED is added to every state that is not terminal.
_ORDINARY_MOVES: Final[dict[CallState, frozenset[CallState]]] = {
    CallState.RECEIVED: frozenset({CallState.ROUTING}),
    # Routing cannot reach escalation: a call is handled before there is anything to escalate.
    CallState.ROUTING: frozenset(
        {CallState.PASSTHROUGH, CallState.AGENT_HANDLING, CallState.REJECTED}
    ),
    CallState.PASSTHROUGH: frozenset({CallState.COMPLETED}),
    CallState.AGENT_HANDLING: frozenset({CallState.ESCALATION_REQUESTED, CallState.COMPLETED}),
    # An escalation abandoned before anyone is dialled returns the call to the agent.
    CallState.ESCALATION_REQUESTED: frozenset(
        {CallState.HUMAN_RINGING, CallState.AGENT_HANDLING, CallState.COMPLETED}
    ),
    # An unanswered or declined ring returns the call to the agent.
    CallState.HUMAN_RINGING: frozenset(
        {CallState.HUMAN_JOINED, CallState.AGENT_HANDLING, CallState.COMPLETED}
    ),
    CallState.HUMAN_JOINED: frozenset({CallState.COMPLETED}),
    CallState.REJECTED: frozenset(),
    CallState.COMPLETED: frozenset(),
    CallState.FAILED: frozenset(),
}

ALLOWED: Final[MappingProxyType[CallState, frozenset[CallState]]] = MappingProxyType(
    {
        state: moves if state in TERMINAL else moves | {CallState.FAILED}
        for state, moves in _ORDINARY_MOVES.items()
    }
)


def is_terminal(state: CallState) -> bool:
    """Whether the call is over, however it ended."""
    return state in TERMINAL


def can_move(current: CallState, requested: CallState) -> bool:
    """Whether this move is permitted, without performing it."""
    return requested in ALLOWED[current]


def move(current: CallState, requested: CallState) -> CallState:
    """Perform the move, or raise naming both states."""
    if not can_move(current, requested):
        raise IllegalTransitionError(current, requested)
    return requested
