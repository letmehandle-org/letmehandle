"""How long a call may last and how many one account may have at once, on each kind of line.

A handset reports its own calls, and a report of one ending that never arrives — the app killed,
the handset offline — would otherwise leave the call's run holding it until the process stopped.
The same is true of any transport whose report of an ending is lost, so both bounds hold on every
line, and each is a transition to FAILED through the one teardown.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from letmehandle.application.orchestration.orchestrator import LIVE_CALLS_PER_ACCOUNT
from letmehandle.application.orchestration.run import CALL_BOUNDED
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.call_transport import ScreeningDecision
from tests.support.orchestration import (
    QUICK,
    HandsetLine,
    Line,
    StreamingLine,
    orchestrating,
)

STRANGER = Caller(number=PhoneNumber("+12025550101"))
PASSING = UserPreferences(rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH))
# Long enough for a call to be put through and picked up first, short enough to wait for. The ring
# is longer, so a streaming call picked up is still up when the duration runs out.
SHORT_CALLS = replace(QUICK, duration=timedelta(seconds=0.5), ring=timedelta(seconds=5))
# Calls put through stay ringing for the length of a test.
LONG_RINGS = replace(QUICK, ring=timedelta(seconds=30))


@pytest.fixture(params=[StreamingLine, HandsetLine], ids=["streaming", "handset"])
def line(request: pytest.FixtureRequest) -> Line:
    made: Line = request.param()
    return made


def arrives(line: Line, call: str) -> None:
    if isinstance(line, HandsetLine):
        line.arrives(call, ScreeningDecision.ALLOW)
    else:
        assert isinstance(line, StreamingLine)
        line.arrives(call, STRANGER)


def picked_up(line: Line, call: str) -> None:
    if isinstance(line, HandsetLine):
        line.picked_up(call)
    else:
        assert isinstance(line, StreamingLine)
        line.user_answers(call)


async def test_a_call_whose_ending_is_never_reported_is_failed_once_it_runs_too_long(
    line: Line,
) -> None:
    async with orchestrating(line, preferences=PASSING, bounds=SHORT_CALLS) as running:
        arrives(line, "call")
        await running.settled("call", CallState.PASSTHROUGH)
        picked_up(line, "call")
        call = await running.ended("call")

        assert call.state is CallState.FAILED
        assert call.ended_at is not None
        assert line.asked("terminate", "call") == 1
        assert running.stores.summaries.stored[call.id].outcome is CallOutcome.FAILED
        assert running.metrics.counted(CALL_BOUNDED, kind="duration") == 1
        assert running.orchestrator.live_calls == 0


async def test_a_call_that_ends_in_time_is_not_ended_again_when_the_bound_passes(
    line: Line,
) -> None:
    async with orchestrating(line, preferences=PASSING, bounds=SHORT_CALLS) as running:
        arrives(line, "call")
        await running.settled("call", CallState.PASSTHROUGH)
        picked_up(line, "call")
        line.hangs_up("call")
        call = await running.ended("call")
        await asyncio.sleep(SHORT_CALLS.duration.total_seconds() * 2)

        assert running.stores.call("call").state is call.state is CallState.COMPLETED
        assert line.asked("terminate", "call") == 1
        assert running.metrics.counted(CALL_BOUNDED) == 0


async def test_a_call_beyond_the_accounts_live_calls_is_failed_and_the_others_carry_on(
    line: Line,
) -> None:
    async with orchestrating(line, preferences=PASSING, bounds=LONG_RINGS) as running:
        allowed = [f"call-{index}" for index in range(LIVE_CALLS_PER_ACCOUNT)]
        for call in allowed:
            arrives(line, call)
            await running.settled(call, CallState.PASSTHROUGH)

        arrives(line, "one-too-many")
        refused = await running.ended("one-too-many")

        assert refused.state is CallState.FAILED
        assert running.stores.states("one-too-many") == [CallState.RECEIVED, CallState.FAILED]
        assert line.asked("terminate", "one-too-many") == 1
        assert running.stores.summaries.stored[refused.id].outcome is CallOutcome.FAILED
        assert running.metrics.counted(CALL_BOUNDED, kind="live_calls") == 1
        assert running.orchestrator.live_calls == LIVE_CALLS_PER_ACCOUNT
        assert all(running.stores.call(call).state is CallState.PASSTHROUGH for call in allowed)

        # Once one of them ends, there is room for the next.
        line.hangs_up(allowed[0])
        await running.ended(allowed[0])
        arrives(line, "next")
        await running.settled("next", CallState.PASSTHROUGH)
