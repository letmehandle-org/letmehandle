"""Things happening to one call at once, or twice, each ending in a legal walk and one teardown."""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest

from letmehandle.application.agent.ports import CallActions, CallEnding
from letmehandle.application.orchestration.run import CallIsOverError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import ALLOWED, CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.call_transport import ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from tests.support.orchestration import (
    OWNERS_NUMBER,
    ROUTINE,
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))
REPEATS = range(15)
IMMEDIATELY = EscalationDecision.needed(
    EscalationReason.CALLER_ASKED_FOR_THE_USER, EscalationUrgency.IMMEDIATE
)


def a_legal_walk(states: list[CallState]) -> bool:
    return states[0] is CallState.RECEIVED and all(
        after in ALLOWED[before] for before, after in pairwise(states)
    )


async def on_the_assistant(running: Running) -> StreamingLine:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(CALL, STRANGER)
    await running.settled(CALL, CallState.AGENT_HANDLING)
    await running.session()
    line.assistant_joins(CALL)
    await eventually(lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT))
    return line


def actions_of(running: Running) -> CallActions:
    agent = running.agent.built
    assert agent is not None
    return agent._actions


async def together(*sides: object) -> None:
    """Start every side as its own task together, wait for all, and raise what any side raised."""
    start = asyncio.Event()

    async def side(work: object) -> None:
        await start.wait()
        if asyncio.iscoroutine(work):
            await work
        else:
            assert callable(work)
            work()

    tasks = [asyncio.get_running_loop().create_task(side(each)) for each in sides]
    await asyncio.sleep(0)
    start.set()
    await asyncio.gather(*tasks)


async def escalated(running: Running) -> StreamingLine:
    line = await on_the_assistant(running)
    actions = actions_of(running)
    await actions.escalate(CallId(CALL), IMMEDIATELY)
    await running.settled(CALL, CallState.HUMAN_RINGING)
    return line


class TestNamedRaces:
    @pytest.mark.parametrize("attempt", REPEATS)
    async def test_the_caller_hangs_up_while_the_user_rings(self, attempt: int) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await escalated(running)
            await together(
                lambda: line.hangs_up(CALL),
                lambda: line.user_unreachable(CALL, ParticipantOutcome.NO_ANSWER),
            )
            call = await running.ended(CALL)

            states = running.stores.states(CALL)
            assert a_legal_walk(states)
            assert call.state is CallState.COMPLETED
            # Ending the call stops the phone ringing: one release, and nobody left on it.
            assert line.asked("terminate", CALL) == 1
            assert running.agent.forgotten == [CallId(CALL)]
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.UNANSWERED_ESCALATION

    @pytest.mark.parametrize("attempt", REPEATS)
    async def test_the_user_answers_as_the_caller_hangs_up(self, attempt: int) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await escalated(running)
            first, second = (
                (line.user_answers, line.hangs_up)
                if attempt % 2
                else (line.hangs_up, line.user_answers)
            )
            await together(lambda: first(CALL), lambda: second(CALL))
            call = await running.ended(CALL)

            states = running.stores.states(CALL)
            assert a_legal_walk(states)
            assert states[-1] is CallState.COMPLETED
            assert states[-2] in {CallState.HUMAN_RINGING, CallState.HUMAN_JOINED}
            # Joined only if the join was heard before the ending, never after it.
            joined = CallState.HUMAN_JOINED in states
            assert call.has_participant(ParticipantRole.HUMAN) is joined
            assert line.asked("terminate", CALL) == 1

    @pytest.mark.parametrize("attempt", REPEATS)
    async def test_escalation_requested_twice_at_once_rings_once(self, attempt: int) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            actions = actions_of(running)
            await together(
                actions.escalate(CallId(CALL), IMMEDIATELY),
                actions.escalate(CallId(CALL), IMMEDIATELY),
            )
            await running.settled(CALL, CallState.HUMAN_RINGING)
            assert line.dialled == [OWNERS_NUMBER]
            assert running.stores.states(CALL).count(CallState.ESCALATION_REQUESTED) == 1

    @pytest.mark.parametrize("attempt", REPEATS)
    async def test_the_agent_ends_the_call_while_the_escalation_is_in_flight(
        self, attempt: int
    ) -> None:
        line = StreamingLine()
        dialling = asyncio.Event()
        line.holding["dial"] = dialling
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            actions = actions_of(running)
            ringing = asyncio.get_running_loop().create_task(
                actions.escalate(CallId(CALL), IMMEDIATELY)
            )
            await eventually(lambda: line.asked("dial", CALL) == 1)
            ending = asyncio.get_running_loop().create_task(
                actions.end_call(CallId(CALL), CallEnding.RESOLVED, ROUTINE)
            )
            await together(dialling.set)
            await asyncio.gather(ringing, ending)
            call = await running.ended(CALL)

            states = running.stores.states(CALL)
            assert a_legal_walk(states)
            # The dial under way finishes before the ending is acted on.
            assert states[-3:] == [
                CallState.ESCALATION_REQUESTED,
                CallState.HUMAN_RINGING,
                CallState.COMPLETED,
            ]
            assert call.escalated_at is not None
            assert line.asked("terminate", CALL) == 1

    async def test_a_callback_after_teardown_changes_nothing_and_starts_nothing(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await escalated(running)
            line.hangs_up(CALL)
            await running.ended(CALL)
            before = list(running.stores.states(CALL))

            line.user_answers(CALL)
            line.leaves(CALL, Leg.ASSISTANT)
            line.hangs_up(CALL)
            line.arrives(CALL, STRANGER)
            await asyncio.sleep(0.05)

            assert running.stores.states(CALL) == before
            assert running.orchestrator.live_calls == 0
            assert line.asked("terminate", CALL) == 1
            assert line.asked("answer", CALL) == 1

    async def test_requests_queued_behind_an_ending_are_told_the_call_is_over(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            actions = actions_of(running)
            ending = actions.end_call(CallId(CALL), CallEnding.RESOLVED, ROUTINE)
            late = actions.take_message(CallId(CALL), "Call me back.")
            outcomes = await asyncio.gather(ending, late, return_exceptions=True)
            assert outcomes[0] is None
            assert isinstance(outcomes[1], CallIsOverError)
            await running.ended(CALL)


class TestRepeats:
    """Every externally triggered event, delivered twice, changes nothing the first did not."""

    async def run_the_whole_call(self, *, duplicating: bool) -> tuple[list[CallState], list[str]]:
        line = StreamingLine()
        line.duplicating = duplicating
        async with orchestrating(line) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            line.assistant_joins(CALL)
            await eventually(
                lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT)
            )
            await actions_of(running).escalate(CallId(CALL), IMMEDIATELY)
            await running.settled(CALL, CallState.HUMAN_RINGING)
            line.user_unreachable(CALL, ParticipantOutcome.BUSY)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            line.leaves(CALL, Leg.USER)
            await actions_of(running).escalate(CallId(CALL), IMMEDIATELY)
            await running.settled(CALL, CallState.HUMAN_RINGING)
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            line.leaves(CALL, Leg.USER)
            call = await running.ended(CALL)
            line.hangs_up(CALL)
            line.report_failure(CALL)
            await asyncio.sleep(0.02)
            return running.stores.states(CALL), [
                f"{each.role.value}:{each.left_at is not None}" for each in call.participants
            ] + [request for request, _ in line.requests]

    async def test_a_whole_call_delivered_twice_is_the_same_call(self) -> None:
        once = await self.run_the_whole_call(duplicating=False)
        twice = await self.run_the_whole_call(duplicating=True)
        assert twice == once

    async def test_an_arrival_delivered_twice_is_one_call(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            arrival = line.arrives(CALL, STRANGER)
            line.repeat(arrival)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            line.repeat(arrival)
            await asyncio.sleep(0.02)
            assert line.asked("answer", CALL) == 1
            assert len(running.speech.sessions) == 1

    async def test_the_same_call_announced_again_under_another_identifier_is_ignored(
        self,
    ) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            line.arrives(CALL, STRANGER)
            await asyncio.sleep(0.02)
            assert running.stores.states(CALL)[-1] is CallState.AGENT_HANDLING
            assert line.asked("answer", CALL) == 1
