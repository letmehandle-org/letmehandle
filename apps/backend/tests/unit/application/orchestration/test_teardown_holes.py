"""Endings that must leave nothing behind: a teardown cut short, and records written after it."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation_context import EscalationStatus
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from tests.support.orchestration import (
    OWNER,
    WANTS_THE_USER,
    Look,
    MemoryCallStores,
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.application.escalation.dispatch import EscalationStores

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))


async def with_the_assistant(running: Running) -> StreamingLine:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(CALL, STRANGER)
    await running.settled(CALL, CallState.AGENT_HANDLING)
    await running.session()
    line.assistant_joins(CALL)
    await eventually(lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT))
    return line


async def test_a_speech_session_that_fails_to_close_still_ends_the_call() -> None:
    line = StreamingLine()
    async with orchestrating(line) as running:
        await with_the_assistant(running)
        session = await running.session()

        async def close() -> None:
            # A session whose reader task died of a defect.
            raise ProviderError("speech", "the reader failed", retryable=False)

        session.close = close  # type: ignore[method-assign]  # a session that fails as it closes
        line.hangs_up(CALL)
        await eventually(lambda: CallId(CALL) not in running.orchestrator._runs)

        assert line.asked("terminate", CALL) == 1
        assert CallId(CALL) in running.stores.summaries.stored
        assert running.stores.call(CALL).state is CallState.COMPLETED
        assert running.metrics.counted("call.speech_close_failed", kind="refused") == 1


async def test_a_speech_session_that_fails_to_close_as_the_assistant_goes_still_ends_the_call() -> (
    None
):
    line = StreamingLine()
    async with orchestrating(line) as running:
        await with_the_assistant(running)
        session = await running.session()

        async def close() -> None:
            raise ProviderError("speech", "the reader failed", retryable=False)

        session.close = close  # type: ignore[method-assign]  # a session that fails as it closes
        # The assistant's leg drops mid-call with nobody else coming.
        line.leaves(CALL, Leg.ASSISTANT)
        await eventually(lambda: CallId(CALL) not in running.orchestrator._runs)

        assert line.asked("terminate", CALL) == 1
        assert CallId(CALL) in running.stores.summaries.stored
        assert running.stores.call(CALL).state is CallState.FAILED


async def test_an_escalation_context_claimed_after_the_call_ended_is_still_marked_ended() -> None:
    line = StreamingLine()
    async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
        claim_may_go = asyncio.Event()
        original = running.escalations.scope
        openings = 0

        @asynccontextmanager
        async def slow_first_opening() -> AsyncIterator[EscalationStores]:
            # The dispatch's first unit of work answers late.
            nonlocal openings
            openings += 1
            if openings == 1:
                await claim_may_go.wait()
            async with original() as stores:
                yield stores

        running.dispatcher._stores = slow_first_opening
        await with_the_assistant(running)
        await running.caller_says("Can I speak to them, please?")
        await running.settled(CALL, CallState.HUMAN_RINGING)

        line.hangs_up(CALL)
        call = await running.ended(CALL)
        claim_may_go.set()
        await running.quiet()

        context = running.escalations.contexts.stored[(OWNER, call.id)]
        assert context.status is EscalationStatus.ENDED


async def test_a_final_save_that_fails_does_not_leave_a_summarised_call_for_recovery_to_fail() -> (
    None
):
    storage = MemoryCallStores()
    await storage.with_owner()
    async with orchestrating(StreamingLine(), stores=storage) as running:
        line = await with_the_assistant(running)
        # One write refused, as a connection reset does.
        storage.calls.refusing_writes = True
        line.hangs_up(CALL)
        await eventually(lambda: CallId(CALL) not in running.orchestrator._runs)
        storage.calls.refusing_writes = False

    async with orchestrating(StreamingLine(), stores=storage):
        # The stored call and its summary agree on how it ended.
        call = storage.call(CALL)
        summary = storage.summaries.stored[CallId(CALL)]
        assert (call.state is CallState.FAILED) == (summary.outcome is CallOutcome.FAILED)


async def test_a_final_save_refused_once_is_tried_again_and_the_call_summarised() -> None:
    storage = MemoryCallStores()
    await storage.with_owner()
    async with orchestrating(StreamingLine(), stores=storage) as running:
        line = await with_the_assistant(running)
        # The ending is the next write of the call: refused once, then answered.
        storage.calls.refusing_next = 1
        line.hangs_up(CALL)
        call = await running.ended(CALL)

        assert call.state is CallState.COMPLETED
        assert storage.summaries.stored[CallId(CALL)].outcome is CallOutcome.CALLER_HUNG_UP
        assert running.metrics.counted("call.storage_failed", stage="final") == 1
