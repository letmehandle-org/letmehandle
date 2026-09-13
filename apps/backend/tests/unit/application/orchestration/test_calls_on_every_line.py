"""What happens to a call, on each kind of line: the same orchestrator, different plans.

Every test here runs over both capability sets. A handset's line never reaches the assistant or an
escalation — not because something refuses, but because its plan has neither.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.models.call import CallHandling, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.call_transport import ScreeningDecision
from tests.support.orchestration import (
    HandsetLine,
    Line,
    StreamingLine,
    orchestrating,
)

STRANGER = Caller(number=PhoneNumber("+12025550101"))
REJECTING = UserPreferences(rules=CallRules(default_posture=HandlingPosture.REJECT))
PASSING = UserPreferences(rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH))


@pytest.fixture(params=[StreamingLine, HandsetLine], ids=["streaming", "handset"])
def line(request: pytest.FixtureRequest) -> Line:
    made: Line = request.param()
    return made


def arrives(line: Line, call: str, *, rejected: bool = False) -> None:
    if isinstance(line, HandsetLine):
        line.arrives(call, ScreeningDecision.REJECT if rejected else ScreeningDecision.ALLOW)
    else:
        assert isinstance(line, StreamingLine)
        line.arrives(call, STRANGER)


async def test_a_call_the_rules_refuse_is_rejected_and_released(line: Line) -> None:
    async with orchestrating(line, preferences=REJECTING) as running:
        arrives(line, "call", rejected=True)
        call = await running.ended("call")

        assert running.stores.states("call") == [
            CallState.RECEIVED,
            CallState.ROUTING,
            CallState.REJECTED,
        ]
        assert call.handling is None
        assert line.asked("terminate", "call") == 1
        assert running.stores.summaries.stored[call.id].outcome is CallOutcome.REJECTED_BY_RULE
        assert running.speech.sessions == []


async def test_a_call_put_through_completes_when_the_caller_hangs_up(line: Line) -> None:
    async with orchestrating(line, preferences=PASSING) as running:
        arrives(line, "call")
        await running.settled("call", CallState.PASSTHROUGH)
        if isinstance(line, StreamingLine):
            line.user_answers("call")
        else:
            assert isinstance(line, HandsetLine)
            line.picked_up("call")
        line.hangs_up("call")
        call = await running.ended("call")

        assert running.stores.states("call") == [
            CallState.RECEIVED,
            CallState.ROUTING,
            CallState.PASSTHROUGH,
            CallState.COMPLETED,
        ]
        assert call.handling is CallHandling.PASSED_THROUGH
        assert [p.role for p in call.participants] == [ParticipantRole.HUMAN]
        assert running.stores.summaries.stored[call.id].outcome is CallOutcome.PASSED_THROUGH
        assert running.speech.sessions == []
