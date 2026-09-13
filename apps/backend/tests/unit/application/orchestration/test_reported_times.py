"""A call reported after the fact is recorded at the moments it happened, not when word of it came.

A handset tells the backend about its calls whenever it next can: seconds later on a good day, hours
later after a day offline. History that put each call at the moment its report arrived would show a
four-second call as starting when it was over and lasting nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from letmehandle.application.orchestration.ledger import REPORTED_CLOCK_SKEW
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.ports.call_transport import CallEventKind, ScreeningDecision
from tests.support.orchestration import HandsetLine, StreamingLine, orchestrating

# What the orchestrator's clock reads throughout: the moment the reports arrive.
NOW: Final = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
PASSING: Final = UserPreferences(rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH))


def rings(line: HandsetLine, call: str, at: datetime, decision: ScreeningDecision) -> None:
    line.report(CallEventKind.INCOMING, call, screening=decision, occurred_at=at)


async def test_a_call_reported_late_keeps_the_moments_the_handset_saw() -> None:
    line = HandsetLine()
    # Rang at 11:59:33, picked up two seconds later, over two seconds after that; reported at noon.
    rang = NOW - timedelta(seconds=27)
    answered = rang + timedelta(seconds=2)
    ended = answered + timedelta(seconds=2)
    async with orchestrating(line, preferences=PASSING) as running:
        rings(line, "call", rang, ScreeningDecision.ALLOW)
        line.report(CallEventKind.ANSWERED, "call", occurred_at=answered)
        line.report(CallEventKind.ENDED, "call", occurred_at=ended)
        call = await running.ended("call")

        assert call.started_at == rang
        assert call.ended_at == ended
        assert call.duration_seconds() == 4
        (human,) = call.participants
        assert (human.role, human.joined_at) == (ParticipantRole.HUMAN, answered)
        marks = running.stores.timeline.marks[CallId("call")]
        assert [(mark.name, mark.at) for mark in marks] == [
            (CallState.RECEIVED.value, rang),
            (CallState.ROUTING.value, rang),
            (CallState.PASSTHROUGH.value, rang),
            (CallState.COMPLETED.value, ended),
        ]
        summary = running.stores.summaries.stored[call.id]
        assert (summary.started_at, summary.ended_at) == (rang, ended)


async def test_a_call_refused_while_the_handset_was_offline_is_placed_hours_back() -> None:
    line = HandsetLine()
    refused = NOW - timedelta(hours=3)
    async with orchestrating(line) as running:
        rings(line, "call", refused, ScreeningDecision.REJECT)
        call = await running.ended("call")

        assert call.state is CallState.REJECTED
        assert (call.started_at, call.ended_at) == (refused, refused)


async def test_a_moment_too_far_ahead_of_this_clock_is_recorded_as_now() -> None:
    # The handset's clock is wrong. Now is the nearest moment that can be true.
    line = HandsetLine()
    async with orchestrating(line, preferences=PASSING) as running:
        rings(
            line, "call", NOW + REPORTED_CLOCK_SKEW + timedelta(seconds=1), ScreeningDecision.ALLOW
        )
        await running.settled("call", CallState.PASSTHROUGH)
        line.report(CallEventKind.ENDED, "call", occurred_at=NOW + timedelta(days=1))
        call = await running.ended("call")

        assert (call.started_at, call.ended_at) == (NOW, NOW)


async def test_a_moment_a_little_ahead_is_an_ordinary_difference_between_clocks() -> None:
    line = HandsetLine()
    ahead = NOW + REPORTED_CLOCK_SKEW
    async with orchestrating(line) as running:
        rings(line, "call", ahead, ScreeningDecision.REJECT)
        call = await running.ended("call")

        assert call.started_at == ahead


async def test_nothing_is_recorded_before_the_call_has_reached_it() -> None:
    # Out of order, or from a clock set back mid-call: an answer or an end earlier than what the
    # call already went through is recorded at that point, so its history never runs backwards.
    line = HandsetLine()
    rang = NOW - timedelta(minutes=1)
    async with orchestrating(line, preferences=PASSING) as running:
        rings(line, "call", rang, ScreeningDecision.ALLOW)
        await running.settled("call", CallState.PASSTHROUGH)
        line.report(CallEventKind.ANSWERED, "call", occurred_at=rang - timedelta(hours=1))
        line.report(CallEventKind.ENDED, "call", occurred_at=rang - timedelta(hours=1))
        call = await running.ended("call")

        assert call.participants[0].joined_at == rang
        assert call.ended_at == rang
        assert running.stores.timeline.marks[CallId("call")][-1].at == rang


async def test_a_streaming_call_is_timed_as_its_events_are_handled() -> None:
    line = StreamingLine()
    async with orchestrating(line, preferences=PASSING) as running:
        line.arrives("call", Caller(number=PhoneNumber("+12025550101")))
        await running.settled("call", CallState.PASSTHROUGH)
        line.user_answers("call")
        line.hangs_up("call")
        call = await running.ended("call")

        assert (call.started_at, call.ended_at) == (NOW, NOW)
        assert call.participants[0].joined_at == NOW
