"""The edges of a call's life: nobody's calls, calls put through, stale news, and stopping."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from letmehandle.application.agent.ports import CallActions, CallEnding
from letmehandle.application.escalation.dispatch import EscalationDispatcher
from letmehandle.application.orchestration import orchestrator as orchestrator_module
from letmehandle.application.orchestration.inputs import RingRanOut
from letmehandle.application.orchestration.orchestrator import CallOrchestrator
from letmehandle.application.orchestration.plan import DialTheUser
from letmehandle.application.orchestration.ports import Bounds
from letmehandle.application.orchestration.run import CallIsOverError
from letmehandle.application.resilience.circuit import Circuits
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.call_transport import ParticipantOutcome, TransportCapabilities
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.observability.tracing import NoTracer
from tests.contracts.fakes import FixedClock
from tests.support.escalation_stores import InMemoryStores
from tests.support.orchestration import (
    ANOTHER_OWNER,
    OWNER,
    OWNERS_NUMBER,
    ROUTINE,
    EveryCallIsTheOwners,
    MemoryCallStores,
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)
from tests.support.recording_metrics import RecordingMetrics

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))
PASSING = UserPreferences(rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH))
IMMEDIATELY = EscalationDecision.needed(
    EscalationReason.CALLER_ASKED_FOR_THE_USER, EscalationUrgency.IMMEDIATE
)
LATER = EscalationDecision.needed(
    EscalationReason.CALLER_ASKED_FOR_THE_USER, EscalationUrgency.WHILE_CONVENIENT
)


class ConversationOnlyLine(StreamingLine):
    """Answers and talks, and has no way to add the user to a call."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_answer_under_program_control=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
        )


class BrokenLine(StreamingLine):
    """Fails in a way no transport is allowed to: an error that is not the domain's."""

    async def terminate(self, call_id: CallId) -> None:
        await super().terminate(call_id)
        raise RuntimeError("a defect in the transport")


async def on_the_assistant(running: Running, call: str = CALL) -> StreamingLine:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(call, STRANGER)
    await running.settled(call, CallState.AGENT_HANDLING)
    await running.session()
    return line


def actions_of(running: Running) -> CallActions:
    agent = running.agent.built
    assert agent is not None
    return agent._actions


class TestCallsNobodyOwns:
    async def test_a_call_for_nobody_is_let_go_and_never_recorded(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            line.arrives("nobody-call", STRANGER)
            await eventually(lambda: line.asked("terminate", "nobody-call") == 1)
            await eventually(lambda: running.orchestrator.live_calls == 0)
            assert running.stores.calls.stored == {}
            assert line.asked("answer", "nobody-call") == 0

    async def test_a_call_for_an_account_that_no_longer_exists_is_let_go(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, stores=MemoryCallStores()) as running:
            line.arrives(CALL, STRANGER)
            await eventually(lambda: line.asked("terminate", CALL) == 1)
            assert running.stores.calls.stored == {}

    async def test_a_call_arriving_while_storage_is_down_is_let_go_and_counted(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            running.stores.unavailable = True
            line.arrives(CALL, STRANGER)
            await eventually(lambda: line.asked("terminate", CALL) == 1)
            assert running.metrics.counted("call.provider_failed", stage="owner") == 1
            assert running.stores.calls.stored == {}


class TestPutThroughOnAStreamingLine:
    async def test_the_user_is_dialled_and_the_call_ends_when_they_hang_up(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, preferences=PASSING) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)
            await eventually(lambda: line.dialled == [OWNERS_NUMBER])
            line.user_answers(CALL)
            await eventually(
                lambda: running.stores.call(CALL).has_participant(ParticipantRole.HUMAN)
            )
            line.leaves(CALL, Leg.USER)
            assert (await running.ended(CALL)).state is CallState.COMPLETED
            assert running.speech.sessions == []

    async def test_a_dial_the_transport_refuses_fails_the_call(self) -> None:
        line = StreamingLine()
        line.refusing.add("dial")
        async with orchestrating(line, preferences=PASSING) as running:
            line.arrives(CALL, STRANGER)
            assert (await running.ended(CALL)).state is CallState.FAILED

    async def test_a_user_who_cannot_be_reached_ends_the_call(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, preferences=PASSING) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)
            line.user_unreachable(CALL, ParticipantOutcome.BUSY)
            assert (await running.ended(CALL)).state is CallState.COMPLETED
            assert line.asked("cancel", CALL) == 0

    async def test_a_ring_nobody_picks_up_is_cancelled_and_the_call_ended(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, preferences=PASSING) as running:
            line.arrives(CALL, STRANGER)
            call = await running.ended(CALL)
            assert call.state is CallState.COMPLETED
            assert line.asked("cancel", CALL) == 1

    async def test_news_of_an_assistant_on_a_call_put_through_changes_nothing(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, preferences=PASSING) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)
            line.report_unreachable_assistant(CALL)
            line.user_answers(CALL)
            await eventually(
                lambda: running.stores.call(CALL).has_participant(ParticipantRole.HUMAN)
            )
            line.user_unreachable(CALL, ParticipantOutcome.NO_ANSWER)
            await asyncio.sleep(0.02)
            assert running.stores.call(CALL).state is CallState.PASSTHROUGH
            line.hangs_up(CALL)
            await running.ended(CALL)


class TestLateAndStaleNews:
    async def test_a_ring_expiry_from_a_cancelled_ring_is_ignored(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            run = running.orchestrator._runs[CallId(CALL)]
            run.post(RingRanOut(DialTheUser(line), generation=999))
            await asyncio.sleep(0.02)
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            assert line.asked("cancel", CALL) == 0

    async def test_a_user_answering_a_ring_already_given_up_on_is_recorded_on_the_call(
        self,
    ) -> None:
        line = StreamingLine()
        # Giving up on the ring could not reach the provider, so the user's phone rang on.
        line.refusing.add("cancel")
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            await actions_of(running).escalate(CallId(CALL), IMMEDIATELY)
            await running.settled(CALL, CallState.HUMAN_RINGING)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            assert line.asked("cancel", CALL) == 1
            session = await running.session()
            line.user_answers(CALL)
            await eventually(
                lambda: running.stores.call(CALL).has_participant(ParticipantRole.HUMAN)
            )
            await eventually(lambda: '"on_the_call"' in session.context_updates[-1])
            # Still the assistant's call: the user leaving does not end it for the caller.
            line.leaves(CALL, Leg.USER)
            await eventually(
                lambda: not running.stores.call(CALL).has_participant(ParticipantRole.HUMAN)
            )
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            line.hangs_up(CALL)
            call = await running.ended(CALL)
            assert [each.role for each in call.participants] == [ParticipantRole.HUMAN]

    async def test_the_assistant_joining_twice_is_one_assistant(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            line.assistant_joins(CALL)
            line.assistant_joins(CALL)
            await eventually(
                lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT)
            )
            await asyncio.sleep(0.02)
            assert len(running.stores.call(CALL).participants) == 1


class TestEscalationsThatRingNothing:
    async def test_an_escalation_for_later_rings_nothing_until_one_is_for_now(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            actions = actions_of(running)
            await actions.escalate(CallId(CALL), LATER)
            assert line.dialled == []
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            await actions.escalate(CallId(CALL), IMMEDIATELY)
            await running.settled(CALL, CallState.HUMAN_RINGING)
            assert line.dialled == [OWNERS_NUMBER]

    async def test_a_line_that_cannot_add_the_user_keeps_the_reason_and_rings_nothing(self) -> None:
        line = ConversationOnlyLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            await actions_of(running).escalate(CallId(CALL), IMMEDIATELY)
            line.hangs_up(CALL)
            await running.ended(CALL)
            assert line.dialled == []
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.escalation_reason is EscalationReason.CALLER_ASKED_FOR_THE_USER

    async def test_a_call_too_long_to_name_in_a_notification_still_rings(self) -> None:
        line = StreamingLine()
        long_call = "c" * 70
        async with orchestrating(line) as running:
            await on_the_assistant(running, long_call)
            await actions_of(running).escalate(CallId(long_call), IMMEDIATELY)
            await running.settled(long_call, CallState.HUMAN_RINGING)
            await running.quiet()
            assert running.notifications.sent == []
            assert line.dialled == [OWNERS_NUMBER]


class TestRememberingEndings:
    async def test_only_so_many_endings_are_remembered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Bounded, because a process runs for weeks. A call forgotten that way and announced again
        # is taken as the new call it would have to be.
        monkeypatch.setattr(orchestrator_module, "REMEMBERED_ENDINGS", 1)
        line = StreamingLine()
        async with orchestrating(line) as running:
            for call in ("first", "second"):
                await on_the_assistant(running, call)
                line.hangs_up(call)
                await running.ended(call)
            line.arrives("second", STRANGER)
            line.arrives("first", STRANGER)
            await eventually(lambda: line.asked("answer", "first") == 2)
            assert line.asked("answer", "second") == 1
            line.hangs_up("first")
            await eventually(lambda: running.orchestrator.live_calls == 0)
            # Taken for a new call, but never written over the record of the one that ended.
            assert running.stores.states("first") == [
                CallState.RECEIVED,
                CallState.ROUTING,
                CallState.AGENT_HANDLING,
                CallState.COMPLETED,
            ]


class TestEndingOneUsersCalls:
    """What deleting an account asks: that user's calls end, and nobody else's."""

    async def test_their_calls_are_torn_down_before_it_returns_and_others_go_on(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await running.stores.users.add(
                User(id=ANOTHER_OWNER, phone_number=PhoneNumber("+12025550144"))
            )
            line.arrives(CALL, STRANGER)
            line.arrives("another-call", STRANGER)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            await running.settled("another-call", CallState.AGENT_HANDLING)

            await running.orchestrator.end_calls_of(OWNER)

            assert running.orchestrator.live_calls == 1
            assert running.stores.call(CALL).state is CallState.FAILED
            assert line.asked("terminate", CALL) == 1
            assert running.stores.call("another-call").state is CallState.AGENT_HANDLING
            assert line.asked("terminate", "another-call") == 0

    async def test_a_teardown_that_hangs_is_cancelled_at_the_bound(self) -> None:
        line = StreamingLine()
        line.holding["terminate"] = asyncio.Event()
        bounds = Bounds(provider=timedelta(seconds=30), shutdown=timedelta(seconds=0.1))
        async with orchestrating(line, bounds=bounds) as running:
            await on_the_assistant(running)

            async with asyncio.timeout(1):
                await running.orchestrator.end_calls_of(OWNER)

            assert running.orchestrator.live_calls == 0

    async def test_a_user_with_no_calls_has_nothing_to_end(self) -> None:
        async with orchestrating(StreamingLine()) as running:
            await running.orchestrator.end_calls_of(OWNER)
            assert running.orchestrator.live_calls == 0


class TestStopping:
    async def test_stopping_mid_call_fails_it_and_releases_it(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            await running.orchestrator.stop()
            call = running.stores.call(CALL)
            assert call.state is CallState.FAILED
            assert line.asked("terminate", CALL) == 1
            session = await running.session()
            assert session.is_closed

    async def test_a_call_whose_teardown_hangs_is_cancelled_at_the_bound_and_left_for_later(
        self,
    ) -> None:
        line = StreamingLine()
        line.holding["terminate"] = asyncio.Event()
        bounds = Bounds(provider=timedelta(seconds=30), shutdown=timedelta(seconds=0.1))
        async with orchestrating(line, bounds=bounds) as running:
            await on_the_assistant(running)
            await running.orchestrator.stop()
            assert running.orchestrator.live_calls == 0
            # Moved to its ending in memory, but never stored as ended: the next start ends it.
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING

    async def test_stopping_what_never_started_is_safe(self) -> None:
        async with orchestrating(StreamingLine(), start=False) as running:
            await running.orchestrator.stop()

    async def test_a_request_for_a_call_being_torn_down_is_refused(self) -> None:
        line = StreamingLine()
        line.holding["terminate"] = asyncio.Event()
        async with orchestrating(line, bounds=Bounds(provider=timedelta(seconds=5))) as running:
            await on_the_assistant(running)
            actions = actions_of(running)
            ending = asyncio.get_running_loop().create_task(
                actions.end_call(CallId(CALL), CallEnding.RESOLVED, ROUTINE)
            )
            await eventually(lambda: line.asked("terminate", CALL) == 1)
            with pytest.raises(CallIsOverError):
                await actions.take_message(CallId(CALL), "Too late.")
            line.holding["terminate"].set()
            await ending
            await running.ended(CALL)

    async def test_a_run_that_breaks_is_let_go_and_its_call_left_for_the_next_start(
        self,
    ) -> None:
        line = BrokenLine()
        async with orchestrating(line) as running:
            await on_the_assistant(running)
            line.hangs_up(CALL)
            await eventually(lambda: running.orchestrator.live_calls == 0)
            assert CallId(CALL) not in running.stores.summaries.stored


def test_a_streaming_line_without_an_assistant_cannot_be_orchestrated() -> None:
    with pytest.raises(InvariantError, match="speech service"):
        CallOrchestrator(
            transport=StreamingLine(),
            ownership=EveryCallIsTheOwners(),
            stores=MemoryCallStores().scope,
            dispatcher=EscalationDispatcher(
                providers=[],
                stores=InMemoryStores().scope,
                metrics=RecordingMetrics(),
                tracer=NoTracer(),
                circuits=Circuits(metrics=RecordingMetrics()),
            ),
            clock=FixedClock(),
            metrics=RecordingMetrics(),
            tracer=NoTracer(),
            circuits=Circuits(metrics=RecordingMetrics()),
            assistant=None,
            summariser=None,
        )


@pytest.mark.parametrize("bound", ["ring", "judgement", "shutdown"])
def test_a_bound_with_no_time_in_it_is_refused(bound: str) -> None:
    with pytest.raises(InvariantError, match=bound):
        Bounds(**{bound: timedelta(0)})
