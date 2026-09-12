"""Where a call can be, and where it can go from there.

The transitions live in one table. Not spread across the code that performs them, because then
the answer to "can this happen?" is a search rather than a read, and two places eventually
disagree.

The table is also the reason a new state cannot be added quietly: `ALLOWED` must name every
member, and a test asserts it, so adding a state without deciding its transitions fails the
build rather than producing a state nothing can leave.
"""

from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Final

from letmehandle.domain.errors import IllegalTransitionError


class CallState(StrEnum):
    """The life of a call.

    `REJECTED` and `COMPLETED` and `FAILED` are endings, and they are different endings:
    rejected means the rules refused it, completed means it ran its course, failed means the
    product broke. Collapsing them would make the difference invisible in history and in
    metrics, which is exactly where it matters.
    """

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


# Every state maps to the complete set it may move to. An empty set is terminal, and is written
# out rather than omitted so that a missing entry is a mistake rather than an ending.
#
# FAILED is reachable from every non-terminal state and is not listed in each one: it is added
# below, because writing it ten times invites the eleventh to be forgotten.
_TRANSITIONS: dict[CallState, set[CallState]] = {
    CallState.RECEIVED: {CallState.ROUTING},
    # Routing decides among three outcomes and nothing else. In particular it cannot reach
    # escalation: a call must be handled before there is anything to escalate about.
    CallState.ROUTING: {
        CallState.PASSTHROUGH,
        CallState.AGENT_HANDLING,
        CallState.REJECTED,
    },
    CallState.PASSTHROUGH: {CallState.COMPLETED},
    CallState.AGENT_HANDLING: {
        CallState.ESCALATION_REQUESTED,
        CallState.COMPLETED,
    },
    # Back to AGENT_HANDLING because an escalation can be abandoned before anyone is dialled —
    # the caller answers their own question, or the agent decides it can finish after all.
    CallState.ESCALATION_REQUESTED: {
        CallState.HUMAN_RINGING,
        CallState.AGENT_HANDLING,
        CallState.COMPLETED,
    },
    # Likewise: an unanswered or declined ring returns the call to the agent rather than ending
    # it. A call that dies because its owner was busy is the failure this product exists to
    # prevent.
    CallState.HUMAN_RINGING: {
        CallState.HUMAN_JOINED,
        CallState.AGENT_HANDLING,
        CallState.COMPLETED,
    },
    CallState.HUMAN_JOINED: {CallState.COMPLETED},
    CallState.REJECTED: set(),
    CallState.COMPLETED: set(),
    CallState.FAILED: set(),
}

TERMINAL: Final[frozenset[CallState]] = frozenset(
    {CallState.REJECTED, CallState.COMPLETED, CallState.FAILED}
)

for _state, _destinations in _TRANSITIONS.items():
    if _state not in TERMINAL:
        _destinations.add(CallState.FAILED)

ALLOWED: Final[MappingProxyType[CallState, frozenset[CallState]]] = MappingProxyType(
    {state: frozenset(destinations) for state, destinations in _TRANSITIONS.items()}
)


def is_terminal(state: CallState) -> bool:
    """Whether the call is over, however it ended."""
    return state in TERMINAL


def can_move(current: CallState, requested: CallState) -> bool:
    """Whether this move is permitted, without performing it."""
    return requested in ALLOWED[current]


def move(current: CallState, requested: CallState) -> CallState:
    """Perform the move, or raise naming both states.

    Raising rather than returning the current state unchanged. A no-op would let a caller
    believe a transition happened, and the bug would surface as a call stuck in a state with no
    indication of why.
    """
    if not can_move(current, requested):
        raise IllegalTransitionError(current, requested)
    return requested
