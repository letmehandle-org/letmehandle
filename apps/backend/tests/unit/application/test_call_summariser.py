"""The summariser keeps a model's draft only when it passes, and the fallback on every failure.

The drafter here is a stand-in that answers, raises or never answers as told, so what is proven is
the summariser's own promise: a valid summary whatever the model does, built from the facts it may
not contradict, within its bound, and logged without a word of the call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import structlog
from structlog.testing import capture_logs

from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.application.calls.summariser import (
    ModelCallSummariser,
    SummaryDrafter,
    SummaryNotWrittenError,
)
from letmehandle.application.calls.summary_draft import DetailKind, DraftDetail, SummaryDraft
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import CallSession
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import CallOutcome, ExtractedDetail
from tests.support.ended_calls import START, STRANGER, Ending, caller_said, ended

if TYPE_CHECKING:
    from collections.abc import Iterator

    from letmehandle.application.calls.summary_draft import SummaryRequest
    from letmehandle.domain.models.summary import CallSummary

SECRET = "the account number is quintessential-walrus-4111"
SAID = caller_said(
    "It's Swift Parcels, the parcel is at 14 Alder Close.",
    f"Also, {SECRET}.",
)
HEADLINE = "Swift Parcels left your parcel at 14 Alder Close, and your assistant noted it."
ADDRESS = DraftDetail(DetailKind.ADDRESS, "14 Alder Close", "the parcel is at 14 Alder Close")
GOOD = SummaryDraft(
    headline=HEADLINE,
    intent=CallIntent.DELIVERY_IN_PROGRESS,
    outcome=CallOutcome.RESOLVED_BY_AGENT,
    details=(ADDRESS,),
)
BOUND = timedelta(seconds=5)


@dataclass
class StandInDrafter(SummaryDrafter):
    """Answers with `answer`, raises it, or never answers when it is None."""

    answer: SummaryDraft | Exception | None
    asked: list[SummaryRequest] = field(default_factory=list)
    abandoned: int = 0
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    async def draft(self, request: SummaryRequest) -> SummaryDraft:
        self.asked.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        if self.answer is None:
            self.waiting.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.abandoned += 1
                raise
        assert isinstance(self.answer, SummaryDraft)
        return self.answer


@pytest.fixture
def unfiltered_logging() -> Iterator[None]:
    # Another test may have configured logging at a level that drops these events.
    configured = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**configured)


async def summarised(
    drafter: StandInDrafter, facts: CallFacts | None = None, *, bound: timedelta = BOUND
) -> CallSummary:
    facts = facts or ended(SAID)
    return await ModelCallSummariser(drafter, timeout=bound).summarise(
        facts, facts.call.transcript, locale="en"
    )


async def test_a_passing_draft_supplies_the_headline_intent_and_details_and_nothing_else() -> None:
    facts = ended(SAID, Ending.HANDED_OVER)
    handed = SummaryDraft(
        headline="Swift Parcels called about your parcel, and your assistant handed it to you.",
        intent=CallIntent.DELIVERY_IN_PROGRESS,
        outcome=CallOutcome.HANDED_TO_USER,
        details=(ADDRESS,),
    )
    known = fallback_summary(facts, locale="en")

    summary = await summarised(StandInDrafter(handed), facts)

    assert summary.headline == handed.headline
    assert summary.intent is CallIntent.DELIVERY_IN_PROGRESS
    assert summary.details == (
        ExtractedDetail("address", "14 Alder Close", "the parcel is at 14 Alder Close"),
    )
    # Everything else is the facts', whatever the model thought.
    assert summary.outcome is CallOutcome.HANDED_TO_USER
    assert (summary.call_id, summary.caller, summary.importance) == (
        known.call_id,
        known.caller,
        CallImportance.ROUTINE,
    )
    assert (summary.started_at, summary.ended_at, summary.human_joined_at) == (
        known.started_at,
        known.ended_at,
        known.human_joined_at,
    )
    assert summary.escalation_reason is known.escalation_reason


async def test_the_model_is_given_the_facts_and_the_whole_call() -> None:
    drafter = StandInDrafter(GOOD)
    facts = ended(SAID)

    await summarised(drafter, facts)

    [request] = drafter.asked
    assert request.known == fallback_summary(facts, locale="en")
    assert request.transcript == facts.call.transcript
    assert request.locale == "en"


async def test_a_call_on_which_nothing_was_said_is_not_sent_to_a_model() -> None:
    drafter = StandInDrafter(GOOD)
    facts = ended(())

    summary = await summarised(drafter, facts)

    assert summary == fallback_summary(facts, locale="en")
    assert drafter.asked == []


@pytest.mark.usefixtures("unfiltered_logging")
@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError(f"the endpoint refused: {SECRET}"),
        SummaryNotWrittenError("the model stopped with end_turn"),
        ValueError(SECRET),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_model_that_fails_produces_the_fallback_logged_by_kind(failure: Exception) -> None:
    with capture_logs() as events:
        summary = await summarised(StandInDrafter(failure))

    assert summary == fallback_summary(ended(SAID), locale="en")
    assert events == [
        {
            "event": "summary.model_failed",
            "call_id": "call-1",
            "failure": type(failure).__name__,
            "log_level": "warning",
        }
    ]
    assert "walrus" not in repr(events)


@pytest.mark.usefixtures("unfiltered_logging")
async def test_a_model_that_never_answers_is_abandoned_within_the_bound() -> None:
    drafter = StandInDrafter(None)
    started = asyncio.get_running_loop().time()

    with capture_logs() as events:
        summary = await summarised(drafter, bound=timedelta(seconds=0.05))

    assert asyncio.get_running_loop().time() - started < 1
    assert summary == fallback_summary(ended(SAID), locale="en")
    assert drafter.abandoned == 1
    assert [event["failure"] for event in events] == ["TimeoutError"]


@pytest.mark.usefixtures("unfiltered_logging")
async def test_a_draft_the_checks_refuse_produces_the_fallback_logged_by_problem() -> None:
    invented = SummaryDraft(
        headline=f"Your assistant heard that {SECRET}.",
        intent=CallIntent.SALES,
        outcome=CallOutcome.FAILED,
        details=(DraftDetail(DetailKind.REFERENCE_NUMBER, "4112", "the account number is 4112"),),
    )

    with capture_logs() as events:
        summary = await summarised(StandInDrafter(invented))

    assert summary == fallback_summary(ended(SAID), locale="en")
    assert events == [
        {
            "event": "summary.draft_refused",
            "call_id": "call-1",
            "problems": ["wrong_outcome", "ungrounded_detail"],
            "log_level": "warning",
        }
    ]
    assert "walrus" not in repr(events)


async def test_cancelling_the_summary_is_not_mistaken_for_a_failed_model() -> None:
    # Orchestration stopping is not a model misbehaving, and swallowing it would hold a teardown.
    drafter = StandInDrafter(None)
    task = asyncio.create_task(summarised(drafter))
    await drafter.waiting.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert drafter.abandoned == 1


async def test_a_call_still_in_progress_is_a_defect_not_a_fallback() -> None:
    live = CallSession(
        id=CallId("call-1"), user_id=UserId("user-1"), caller=STRANGER, started_at=START
    )
    summariser = ModelCallSummariser(StandInDrafter(GOOD), timeout=BOUND)

    with pytest.raises(InvariantError, match="ended"):
        await summariser.summarise(CallFacts(live), ended(SAID).call.transcript, locale="en")


@pytest.mark.parametrize("seconds", [0, -1])
def test_a_summary_with_no_time_to_be_written_in_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="time"):
        ModelCallSummariser(StandInDrafter(GOOD), timeout=timedelta(seconds=seconds))
