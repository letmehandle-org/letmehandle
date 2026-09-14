"""One orchestrator, several lines: each call lives on the line it arrived on (D-041)."""

from __future__ import annotations

import pytest

from letmehandle.application.escalation.dispatch import EscalationDispatcher
from letmehandle.application.orchestration.orchestrator import CallOrchestrator
from letmehandle.application.resilience.circuit import Circuits
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.observability.tracing import NoTracer
from tests.contracts.fakes import FixedClock
from tests.support.escalation_stores import InMemoryStores
from tests.support.orchestration import (
    OWNERS_NUMBER,
    WANTS_THE_USER,
    Look,
    MemoryCallStores,
    StreamingLine,
    orchestrating,
)
from tests.support.recording_metrics import RecordingMetrics
from tests.unit.application.orchestration.test_recovery import left_ringing, stores_holding

STRANGER = Caller(number=PhoneNumber("+12025550101"))


async def test_calls_on_two_lines_run_together_and_each_is_acted_on_where_it_arrived() -> None:
    first, second = StreamingLine(), StreamingLine()
    async with orchestrating(
        first, other_lines=[second], looks=[Look(proposal=WANTS_THE_USER)]
    ) as running:
        first.arrives("on-first", STRANGER)
        await running.settled("on-first", CallState.AGENT_HANDLING)
        second.arrives("on-second", STRANGER)
        await running.settled("on-second", CallState.AGENT_HANDLING)
        assert running.orchestrator.live_calls == 2

        # Only the second call's caller asks for the user, and only its line rings them.
        await running.caller_says("Can I speak to them, please?", index=1)
        await running.settled("on-second", CallState.HUMAN_RINGING)
        assert second.dialled == [OWNERS_NUMBER]
        assert first.dialled == []

        first.hangs_up("on-first")
        second.hangs_up("on-second")
        await running.ended("on-first")
        await running.ended("on-second")

        assert (first.asked("terminate", "on-first"), first.asked("terminate", "on-second")) == (
            1,
            0,
        )
        assert (second.asked("terminate", "on-second"), second.asked("terminate", "on-first")) == (
            1,
            0,
        )
        assert running.stores.summaries.stored[CallId("on-first")].outcome is not None
        assert running.stores.summaries.stored[CallId("on-second")].outcome is not None


async def test_a_call_left_unfinished_is_ended_on_every_line_and_recorded_once() -> None:
    # Every line is asked to end a stored call, and one refusing does not stop the next.
    first, second = StreamingLine(), StreamingLine()
    first.refusing.add("terminate")
    stores = await stores_holding(left_ringing("left"))
    async with orchestrating(first, other_lines=[second], stores=stores) as running:
        assert first.asked("terminate", "left") == 1
        assert second.asked("terminate", "left") == 1
        assert stores.call("left").state is CallState.FAILED
        assert running.stores.summaries.stored[CallId("left")].outcome is CallOutcome.FAILED


def test_an_orchestrator_with_no_line_is_refused() -> None:
    metrics = RecordingMetrics()
    with pytest.raises(InvariantError, match="a line"):
        CallOrchestrator(
            lines=[],
            stores=MemoryCallStores().scope,
            dispatcher=EscalationDispatcher(
                providers=[],
                stores=InMemoryStores().scope,
                metrics=metrics,
                tracer=NoTracer(),
                circuits=Circuits(metrics=metrics),
            ),
            clock=FixedClock(),
            metrics=metrics,
            tracer=NoTracer(),
            circuits=Circuits(metrics=metrics),
            assistant=None,
            summariser=None,
        )
