"""A restart ends every call the stopped process left behind, as failed, and never leaves one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.models.call import CallHandling, CallSession, Participant, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationContext, EscalationStatus
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE
from tests.contracts.preference_fakes import InMemoryPreferencesRepository
from tests.support.orchestration import (
    OWNER,
    HandsetLine,
    Line,
    MemoryCallStores,
    StreamingLine,
    orchestrating,
)

STARTED = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)


def left_ringing(call_id: str, *, minutes: int = 0) -> CallSession:
    started = STARTED + timedelta(minutes=minutes)
    return CallSession.restore(
        id=CallId(call_id),
        user_id=OWNER,
        caller=Caller(),
        started_at=started,
        state=CallState.HUMAN_RINGING,
        participants=(Participant(ParticipantRole.AGENT, started),),
        ended_at=None,
        handling=CallHandling.ASSISTANT,
        escalated_at=started,
    )


async def stores_holding(*calls: CallSession) -> MemoryCallStores:
    stores = MemoryCallStores()
    await stores.with_owner(UserPreferences(locale="en-GB"))
    for call in calls:
        await stores.calls.save(call)
    return stores


@pytest.fixture(params=[StreamingLine, HandsetLine], ids=["streaming", "handset"])
def line(request: pytest.FixtureRequest) -> Line:
    made: Line = request.param()
    return made


async def test_every_unfinished_call_is_ended_at_its_transport_and_recorded_failed(
    line: Line,
) -> None:
    stores = await stores_holding(left_ringing("left-1"), left_ringing("left-2", minutes=1))
    async with orchestrating(line, stores=stores, start=False) as running:
        await running.escalations.contexts.claim(
            OWNER,
            EscalationContext(
                CallId("left-1"), EscalationReason.CALLER_ASKED_FOR_THE_USER, raised_at=STARTED
            ),
        )
        await running.orchestrator.start()

        for call_id in ("left-1", "left-2"):
            call = stores.call(call_id)
            assert call.state is CallState.FAILED
            assert line.asked("terminate", call_id) >= 1
            assert stores.summaries.stored[call.id].outcome is CallOutcome.FAILED
        assert await stores.calls.unfinished(limit=MAX_CALL_PAGE) == ()
        context = running.escalations.contexts.stored[(OWNER, CallId("left-1"))]
        assert context.status is EscalationStatus.ENDED


async def test_calls_beyond_one_page_are_all_ended() -> None:
    calls = [
        left_ringing(f"left-{number:03}", minutes=number) for number in range(MAX_CALL_PAGE + 5)
    ]
    stores = await stores_holding(*calls)
    async with orchestrating(StreamingLine(), stores=stores) as running:
        assert await stores.calls.unfinished(limit=MAX_CALL_PAGE) == ()
        assert running.metrics.counted("call.recovered", outcome="failed") == MAX_CALL_PAGE + 5


async def test_a_transport_that_cannot_let_a_call_go_does_not_keep_it_unfinished() -> None:
    line = StreamingLine()
    line.refusing.add("terminate")
    stores = await stores_holding(left_ringing("left"))
    async with orchestrating(line, stores=stores):
        assert stores.call("left").state is CallState.FAILED


async def test_storage_that_cannot_be_read_leaves_the_calls_for_the_next_start() -> None:
    stores = await stores_holding(left_ringing("left"))
    stores.unavailable = True
    async with orchestrating(StreamingLine(), stores=stores) as running:
        stores.unavailable = False
        assert stores.call("left").state is CallState.HUMAN_RINGING
        assert running.metrics.counted("call.recovered", outcome="unavailable") == 1


async def test_storage_that_refuses_the_endings_stops_trying_rather_than_spinning() -> None:
    stores = await stores_holding(left_ringing("left"))
    stores.calls.refusing_writes = True
    async with orchestrating(StreamingLine(), stores=stores) as running:
        assert running.metrics.counted("call.recovered", outcome="failed") == 1
        assert stores.calls.stored[CallId("left")].state is CallState.HUMAN_RINGING


class UnreadablePreferences(InMemoryPreferencesRepository):
    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        raise ConnectionError("the preferences could not be read")


async def test_a_summary_without_the_user_s_language_is_written_in_the_default_one() -> None:
    stores = MemoryCallStores(preferences=UnreadablePreferences())
    await stores.with_owner()
    await stores.calls.save(left_ringing("left"))
    async with orchestrating(StreamingLine(), stores=stores):
        assert stores.summaries.stored[CallId("left")].outcome is CallOutcome.FAILED
