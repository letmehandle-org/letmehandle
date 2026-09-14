"""What a call leaves for diagnosis, and how it is handled while a dependency keeps failing."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from letmehandle.application.escalation.dispatch import DELIVERY_SECONDS
from letmehandle.application.orchestration.ledger import STATE_SECONDS
from letmehandle.application.orchestration.metrics import (
    DEGRADED,
    DUPLICATE_IGNORED,
    ESCALATION_RESOLVED,
    JUDGEMENT_FAILED,
    JUDGEMENT_SECONDS,
    PROVIDER_FAILED,
    PROVIDER_SECONDS,
    ROUTED,
    SPEECH_OPEN_SECONDS,
    SUMMARY_SECONDS,
)
from letmehandle.application.resilience.circuit import CircuitPolicy, CircuitState, Dependency
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind, ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from tests.contracts.fakes import FixedClock
from tests.support.orchestration import (
    OWNERS_NUMBER,
    WANTS_THE_USER,
    Look,
    MemoryCallStores,
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))
# Opens on the first failure, so a test needs one call to open it and the next to find it open.
TRIGGER_HAPPY = CircuitPolicy(failures_to_open=1, cool_off=timedelta(minutes=5))


async def with_the_assistant(running: Running, call: str = CALL) -> StreamingLine:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(call, STRANGER)
    await running.settled(call, CallState.AGENT_HANDLING)
    await running.session()
    line.assistant_joins(call)
    await eventually(lambda: running.stores.call(call).has_participant(ParticipantRole.AGENT))
    return line


async def an_escalated_call_the_user_joins(running: Running) -> None:
    line = await with_the_assistant(running)
    await running.caller_says("Is she there? It is urgent.")
    await running.settled(CALL, CallState.HUMAN_RINGING)
    line.user_answers(CALL)
    await running.settled(CALL, CallState.HUMAN_JOINED)
    line.hangs_up(CALL)
    await running.ended(CALL)
    await running.quiet()


def labels_of(running: Running, name: str, key: str) -> set[str]:
    return {each.labels[key] for each in running.metrics.observations if each.name == name}


class TestTheTrace:
    async def test_one_call_is_one_tree_of_spans_under_its_id(self) -> None:
        async with orchestrating(StreamingLine(), looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await an_escalated_call_the_user_joins(running)

        tracer = running.tracer
        [root] = tracer.named("call")
        assert root.attributes == {"call.id": CALL}
        assert root.parent is None
        assert all(span.ended and span.failure is None for span in tracer.spans)
        # Every span of the call is inside it, provider calls included.
        assert all(span.ancestors()[-1:] == ["call"] for span in tracer.spans if span is not root)
        assert {span.name for span in tracer.spans} == {
            "call",
            "call.routing",
            "telephony.answer",
            "speech.open",
            "agent.judgement",
            "call.escalation",
            "telephony.dial",
            "notification.delivery",
            "call.teardown",
            "telephony.terminate",
            "summary.write",
        }
        [routing] = tracer.named("call.routing")
        assert routing.attributes == {"call.route": "assistant"}
        # A provider's latency is attributed to the step that asked for it.
        assert tracer.named("telephony.dial")[0].ancestors() == ["call.escalation", "call"]
        assert tracer.named("notification.delivery")[0].ancestors() == ["call.escalation", "call"]
        assert tracer.named("telephony.terminate")[0].ancestors() == ["call.teardown", "call"]
        assert tracer.named("summary.write")[0].ancestors() == ["call.teardown", "call"]

    async def test_a_failure_is_marked_on_its_span_by_kind(self) -> None:
        line = StreamingLine()
        line.refusing.add("answer")
        async with orchestrating(line) as running:
            line.arrives(CALL, STRANGER)
            await running.ended(CALL)

        [answer] = running.tracer.named("telephony.answer")
        assert answer.failure is not None
        assert answer.failure.value == "refused"


class TestTheMeasurements:
    async def test_every_provider_boundary_and_every_state_a_call_passed_through_is_timed(
        self,
    ) -> None:
        async with orchestrating(StreamingLine(), looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await an_escalated_call_the_user_joins(running)

        metrics = running.metrics
        assert labels_of(running, PROVIDER_SECONDS, "stage") == {"answer", "dial", "terminate"}
        assert labels_of(running, SPEECH_OPEN_SECONDS, "outcome") == {"opened"}
        assert labels_of(running, JUDGEMENT_SECONDS, "outcome") == {"judged"}
        assert labels_of(running, SUMMARY_SECONDS, "outcome") == {"written"}
        assert labels_of(running, DELIVERY_SECONDS, "platform") == {"ios"}
        assert labels_of(running, STATE_SECONDS, "outcome") == {
            "received",
            "routing",
            "agent_handling",
            "escalation_requested",
            "human_ringing",
            "human_joined",
        }
        assert all(value >= 0 for value in metrics.observed(STATE_SECONDS))
        assert metrics.counted(ROUTED, outcome="assistant") == 1
        assert metrics.counted(ESCALATION_RESOLVED, outcome="answered") == 1

    async def test_a_call_that_is_nobodys_is_counted_as_routed_to_nobody(self) -> None:
        line = StreamingLine()
        line.refusing.add("terminate")
        async with orchestrating(line) as running:
            line.arrives("nobody-call", STRANGER)
            await eventually(lambda: running.metrics.counted(ROUTED, outcome="nobody") == 1)

        # Letting it go failed and was counted, with no record to mark it on.
        assert running.metrics.counted(PROVIDER_FAILED, stage="terminate", kind="refused") == 1
        assert "nobody-call" not in {each.value for each in running.stores.timeline.marks}

    @pytest.mark.parametrize(
        ("happens", "outcome"),
        [
            ("rings_out", "no_answer"),
            ("busy", "busy"),
            ("dial_refused", "dial_refused"),
            ("caller_leaves", "call_ended"),
        ],
    )
    async def test_how_an_escalation_resolved_is_counted(self, happens: str, outcome: str) -> None:
        line = StreamingLine()
        if happens == "dial_refused":
            line.refusing.add("dial")
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await with_the_assistant(running)
            await running.caller_says("Is she there? It is urgent.")
            if happens == "busy":
                await running.settled(CALL, CallState.HUMAN_RINGING)
                line.user_unreachable(CALL, ParticipantOutcome.BUSY)
            elif happens == "caller_leaves":
                await running.settled(CALL, CallState.HUMAN_RINGING)
                line.hangs_up(CALL)
            await eventually(
                lambda: running.metrics.counted(ESCALATION_RESOLVED, outcome=outcome) == 1
            )
            line.hangs_up(CALL)
            await running.ended(CALL)

    async def test_a_repeated_event_and_one_arriving_after_the_ending_are_counted_and_ignored(
        self,
    ) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            line.repeat(line.assistant_joins(CALL))
            await eventually(
                lambda: running.metrics.counted(DUPLICATE_IGNORED, stage="repeated") == 1
            )
            ending = line.hangs_up(CALL)
            await running.ended(CALL)
            await eventually(lambda: running.orchestrator.live_calls == 0)
            line.repeat(ending)
            await eventually(lambda: running.metrics.counted(DUPLICATE_IGNORED, stage="late") == 1)


class TestStandings:
    async def test_a_live_call_shows_the_state_it_is_in_and_since_when(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            [standing] = running.orchestrator.standings()

            assert standing.call_id == CallId(CALL)
            assert standing.state is CallState.AGENT_HANDLING
            assert standing.since == FixedClock().now()

            line.hangs_up(CALL)
            await running.ended(CALL)
            await eventually(lambda: running.orchestrator.live_calls == 0)
            assert running.orchestrator.standings() == ()

    async def test_a_call_not_yet_matched_to_its_owner_has_no_standing_yet(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            arrival = CallEvent(CallEventKind.INCOMING, CallId(CALL), EventId("arrival"))
            running.orchestrator.receive(arrival, line)

            # The run exists, and has not yet looked up whose call it is.
            assert running.orchestrator.live_calls == 1
            assert running.orchestrator.standings() == ()
            await running.settled(CALL, CallState.AGENT_HANDLING)
            line.hangs_up(CALL)
            await running.ended(CALL)

    async def test_the_oldest_standing_comes_first(self) -> None:
        line = StreamingLine()
        async with orchestrating(line) as running:
            await with_the_assistant(running, "first")
            await with_the_assistant(running, "second")

            standings = running.orchestrator.standings()
            assert [each.call_id.value for each in standings] == ["first", "second"]
            line.hangs_up("first")
            line.hangs_up("second")
            await running.ended("first")
            await running.ended("second")


class TestTelephonyFailing:
    async def test_a_failing_transport_opens_its_circuit_and_the_next_call_does_not_wait_on_it(
        self,
    ) -> None:
        line = StreamingLine()
        line.failing.update({"answer", "terminate"})
        async with orchestrating(line, circuit_policy=TRIGGER_HAPPY) as running:
            line.arrives("first", STRANGER)
            await running.ended("first")
            line.arrives("second", STRANGER)
            await running.ended("second")

            assert running.circuits.states()["telephony"] is CircuitState.OPEN
            assert line.asked("answer", "first") == 1
            assert line.asked("answer", "second") == 0
            assert running.stores.call("second").state is CallState.FAILED
            assert running.metrics.counted(PROVIDER_FAILED, kind="circuit_open") >= 1

    async def test_ending_a_call_at_the_transport_is_tried_again_and_dialling_never_is(
        self,
    ) -> None:
        line = StreamingLine()
        line.failing.update({"dial", "terminate"})
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await with_the_assistant(running)
            await running.caller_says("Is she there? It is urgent.")
            await eventually(
                lambda: running.metrics.counted(ESCALATION_RESOLVED, outcome="dial_refused") == 1
            )
            line.hangs_up(CALL)
            await running.ended(CALL)

        assert line.asked("dial", CALL) == 1
        assert line.asked("terminate", CALL) == 3
        assert line.dialled == [OWNERS_NUMBER]


class TestSpeechFailing:
    async def test_with_speech_failing_the_next_call_is_put_through_to_the_user(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, circuit_policy=TRIGGER_HAPPY) as running:
            running.speech.refusing = True
            line.arrives("first", STRANGER)
            first = await running.ended("first")
            running.speech.refusing = False

            line.arrives("second", STRANGER)
            second = await running.settled("second", CallState.PASSTHROUGH)

            assert first.state is CallState.FAILED
            assert second.state is CallState.PASSTHROUGH
            assert line.asked("answer", "second") == 0
            assert line.dialled == [OWNERS_NUMBER]
            assert running.metrics.counted(DEGRADED, stage="speech") == 1
            assert running.metrics.counted(ROUTED, outcome="pass_through") == 1
            assert labels_of(running, SPEECH_OPEN_SECONDS, "outcome") == {"failed"}
            line.hangs_up("second")
            await running.ended("second")


class TestTheModelFailing:
    async def test_with_the_model_failing_judgements_are_not_asked_and_summaries_are_the_facts(
        self,
    ) -> None:
        down = ProviderError("model", "unreachable", retryable=True)
        async with orchestrating(
            StreamingLine(), looks=[Look(fails=down)], circuit_policy=TRIGGER_HAPPY
        ) as running:
            line = await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(lambda: running.metrics.counted(JUDGEMENT_FAILED) == 1)
            await running.caller_says("Is anybody there?")
            await eventually(
                lambda: running.metrics.counted(JUDGEMENT_FAILED, kind="circuit_open") == 1
            )
            line.hangs_up(CALL)
            await running.ended(CALL)

        assert running.judgements == 1
        assert running.circuits.states()[Dependency.MODEL.value] is CircuitState.OPEN
        assert running.summariser.asked == []
        assert running.metrics.counted(DEGRADED, stage="summary") == 1
        assert labels_of(running, JUDGEMENT_SECONDS, "outcome") == {"failed"}

    async def test_a_judgement_that_times_out_counts_against_the_model(self) -> None:
        async with orchestrating(
            StreamingLine(),
            looks=[Look(waits_for=asyncio.Event())],
            circuit_policy=TRIGGER_HAPPY,
        ) as running:
            line = await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(lambda: running.metrics.counted(JUDGEMENT_FAILED, kind="timeout") == 1)
            line.hangs_up(CALL)
            await running.ended(CALL)

        assert running.circuits.states()["model"] is CircuitState.OPEN


class TestTheTimeline:
    """Each call's stored timeline says what it moved through and what failed, and nothing else."""

    async def test_an_escalated_call_is_stored_as_every_state_it_entered_in_order(self) -> None:
        async with orchestrating(StreamingLine(), looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await an_escalated_call_the_user_joins(running)

        assert running.stores.marks(CALL) == [
            (MarkKind.TRANSITION, state)
            for state in (
                "received",
                "routing",
                "agent_handling",
                "escalation_requested",
                "human_ringing",
                "human_joined",
                "completed",
            )
        ]

    async def test_a_refused_dial_is_marked_where_it_happened(self) -> None:
        line = StreamingLine()
        line.refusing.add("dial")
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await with_the_assistant(running)
            await running.caller_says("Is she there? It is urgent.")
            await eventually(
                lambda: running.metrics.counted(ESCALATION_RESOLVED, outcome="dial_refused") == 1
            )
            line.hangs_up(CALL)
            await running.ended(CALL)

        marks = running.stores.marks(CALL)
        dial = marks.index((MarkKind.FAILURE, "dial.refused"))
        assert marks[dial - 1] == (MarkKind.TRANSITION, "escalation_requested")
        assert marks[dial + 1] == (MarkKind.TRANSITION, "agent_handling")

    async def test_a_call_put_through_because_speech_was_failing_says_so(self) -> None:
        line = StreamingLine()
        async with orchestrating(line, circuit_policy=TRIGGER_HAPPY) as running:
            running.speech.refusing = True
            line.arrives("first", STRANGER)
            await running.ended("first")
            running.speech.refusing = False
            line.arrives("second", STRANGER)
            await running.settled("second", CallState.PASSTHROUGH)
            line.hangs_up("second")
            await running.ended("second")

        assert (MarkKind.FAILURE, "speech.unavailable") in running.stores.marks("first")
        assert running.stores.marks("second")[:3] == [
            (MarkKind.DEGRADED, "speech"),
            (MarkKind.TRANSITION, "received"),
            (MarkKind.TRANSITION, "routing"),
        ]

    async def test_a_failed_judgement_and_a_summary_without_the_model_are_marked(self) -> None:
        down = ProviderError("model", "unreachable", retryable=True)
        async with orchestrating(
            StreamingLine(), looks=[Look(fails=down)], circuit_policy=TRIGGER_HAPPY
        ) as running:
            line = await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(lambda: running.metrics.counted(JUDGEMENT_FAILED) == 1)
            line.hangs_up(CALL)
            await running.ended(CALL)

        marks = running.stores.marks(CALL)
        assert (MarkKind.FAILURE, "judgement.unavailable") in marks
        assert marks[-2:] == [(MarkKind.TRANSITION, "completed"), (MarkKind.DEGRADED, "summary")]

    async def test_a_write_that_failed_is_marked_on_the_next_one_that_did_not(self) -> None:
        storage = MemoryCallStores()
        await storage.with_owner()
        async with orchestrating(StreamingLine(), stores=storage) as running:
            line = await with_the_assistant(running)
            storage.calls.refusing_next = 1
            line.leaves(CALL, Leg.ASSISTANT)
            await running.ended(CALL)

        marks = storage.marks(CALL)
        assert (MarkKind.FAILURE, "storage.leave.unavailable") in marks
        assert marks.count((MarkKind.TRANSITION, "failed")) == 1
