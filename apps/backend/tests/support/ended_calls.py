"""Calls that have ended, each walked through the real state machine, for summarising.

A call is built the way orchestration leaves one at teardown: the assistant took it, what was said
is on it in order, and it ended one of the ways a summary distinguishes. The facts come back beside
it, as orchestration hands them to the summariser.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import CallFacts
from letmehandle.domain.models.call import CallSession, ParticipantRole, Speaker
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber

if TYPE_CHECKING:
    from collections.abc import Sequence

START: Final = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
# Reserved for fiction, never routable.
STRANGER: Final = Caller(number=PhoneNumber("+12025550123"))


class Ending(StrEnum):
    """How a call the assistant took came to an end."""

    RESOLVED = "resolved"
    HANDED_OVER = "handed_over"
    UNANSWERED = "unanswered"
    CALLER_HUNG_UP = "caller_hung_up"
    FAILED = "failed"


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def ended(
    said: Sequence[tuple[Speaker, str]],
    ending: Ending = Ending.RESOLVED,
    *,
    caller: Caller = STRANGER,
) -> CallFacts:
    """A call on which `said` was said, in order, that then ended as `ending`."""
    call = CallSession(
        id=CallId("call-1"), user_id=UserId("user-1"), caller=caller, started_at=START
    )
    call.move_to(CallState.ROUTING)
    call.move_to(CallState.AGENT_HANDLING)
    call.add_participant(ParticipantRole.AGENT, at(1))
    for second, (speaker, text) in enumerate(said, start=2):
        call.record(speaker, text, at(second))
    reason = None
    if ending in (Ending.HANDED_OVER, Ending.UNANSWERED):
        reason = EscalationReason.CALLER_ASKED_FOR_THE_USER
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=at(30))
        call.move_to(CallState.HUMAN_RINGING)
        if ending is Ending.HANDED_OVER:
            call.move_to(CallState.HUMAN_JOINED)
            call.add_participant(ParticipantRole.HUMAN, at(40))
    call.move_to(
        CallState.FAILED if ending is Ending.FAILED else CallState.COMPLETED, at_instant=at(90)
    )
    return CallFacts(call, escalation_reason=reason, caller_hung_up=ending is Ending.CALLER_HUNG_UP)


def caller_said(*lines: str) -> tuple[tuple[Speaker, str], ...]:
    """A call on which only the caller spoke."""
    return tuple((Speaker.CALLER, line) for line in lines)
