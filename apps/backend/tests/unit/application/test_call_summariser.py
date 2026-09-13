"""The summariser keeps a passing draft, corrects once in its bound, else falls back."""

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
    SUMMARY_WRITTEN,
    ModelCallSummariser,
    SummaryDrafter,
    SummaryNotWrittenError,
)
from letmehandle.application.calls.summary_checks import DraftProblem
from letmehandle.application.calls.summary_draft import (
    DetailKind,
    DraftCorrection,
    DraftDetail,
    SummaryDraft,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import CallSession
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import CallOutcome, ExtractedDetail
from tests.support.ended_calls import START, STRANGER, Ending, caller_said, ended
from tests.support.recording_metrics import RecordingMetrics

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
# A headline copied from what the caller said.
COPIED = SummaryDraft(
    headline="It's Swift Parcels, the parcel is at 14 Alder Close, and your assistant noted it.",
    intent=CallIntent.DELIVERY_IN_PROGRESS,
    outcome=CallOutcome.RESOLVED_BY_AGENT,
    details=(ADDRESS,),
)
INVENTED = SummaryDraft(
    headline=f"Your assistant heard that {SECRET}.",
    intent=CallIntent.SALES,
    outcome=CallOutcome.FAILED,
    details=(DraftDetail(DetailKind.REFERENCE_NUMBER, "4112", "the account number is 4112"),),
)
BOUND = timedelta(seconds=5)

type Answer = SummaryDraft | Exception | None


@dataclass
class HeldClock:
    """Monotonic seconds that pass only when a test moves them."""

    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@dataclass
class StandInDrafter(SummaryDrafter):
    """Gives `answers` in turn (draft, error, or None for never), each taking `takes` seconds."""

    answers: list[Answer]
    takes: float = 0.0
    clock: HeldClock = field(default_factory=HeldClock)
    asked: list[tuple[SummaryRequest, DraftCorrection | None]] = field(default_factory=list)
    abandoned: int = 0
    waiting: asyncio.Event = field(default_factory=asyncio.Event)

    async def draft(
        self, request: SummaryRequest, correction: DraftCorrection | None = None
    ) -> SummaryDraft:
        self.asked.append((request, correction))
        self.clock.now += self.takes
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            self.waiting.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.abandoned += 1
                raise
        assert isinstance(answer, SummaryDraft)
        return answer


@pytest.fixture
def unfiltered_logging() -> Iterator[None]:
    # Resets logging so no configured level drops the events.
    configured = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**configured)


async def summarised(
    drafter: StandInDrafter,
    facts: CallFacts | None = None,
    *,
    bound: timedelta = BOUND,
    metrics: RecordingMetrics | None = None,
) -> CallSummary:
    facts = facts or ended(SAID)
    summariser = ModelCallSummariser(
        drafter, timeout=bound, metrics=metrics or RecordingMetrics(), clock=drafter.clock
    )
    return await summariser.summarise(facts, facts.call.transcript, locale="en")


def written(metrics: RecordingMetrics) -> list[str]:
    """How each summary was written, in the order the summariser counted them."""
    return [each.labels["outcome"] for each in metrics.counts if each.name == SUMMARY_WRITTEN]


async def test_a_passing_draft_supplies_the_headline_intent_and_details_and_nothing_else() -> None:
    facts = ended(SAID, Ending.HANDED_OVER)
    handed = SummaryDraft(
        headline="Swift Parcels called about your parcel, and your assistant handed it to you.",
        intent=CallIntent.DELIVERY_IN_PROGRESS,
        outcome=CallOutcome.HANDED_TO_USER,
        details=(ADDRESS,),
    )
    known = fallback_summary(facts, locale="en")

    summary = await summarised(StandInDrafter([handed]), facts)

    assert summary.headline == handed.headline
    assert summary.intent is CallIntent.DELIVERY_IN_PROGRESS
    assert summary.details == (
        ExtractedDetail("address", "14 Alder Close", "the parcel is at 14 Alder Close"),
    )
    # Everything else comes from the facts.
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
    drafter = StandInDrafter([GOOD])
    facts = ended(SAID)

    await summarised(drafter, facts)

    [(request, correction)] = drafter.asked
    assert request.known == fallback_summary(facts, locale="en")
    assert request.transcript == facts.call.transcript
    assert request.locale == "en"
    assert correction is None


async def test_a_draft_that_passes_is_kept_without_asking_again() -> None:
    drafter = StandInDrafter([GOOD, GOOD])
    metrics = RecordingMetrics()

    summary = await summarised(drafter, metrics=metrics)

    assert summary.headline == HEADLINE
    assert len(drafter.asked) == 1
    assert written(metrics) == ["first_draft"]


async def test_a_call_on_which_nothing_was_said_is_not_sent_to_a_model() -> None:
    drafter = StandInDrafter([GOOD])
    facts = ended(())
    metrics = RecordingMetrics()

    summary = await summarised(drafter, facts, metrics=metrics)

    assert summary == fallback_summary(facts, locale="en")
    assert drafter.asked == []
    assert written(metrics) == []


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
    drafter = StandInDrafter([failure, GOOD])
    metrics = RecordingMetrics()

    with capture_logs() as events:
        summary = await summarised(drafter, metrics=metrics)

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
    # A failed model is not asked again.
    assert len(drafter.asked) == 1
    assert written(metrics) == ["fallback"]


@pytest.mark.usefixtures("unfiltered_logging")
async def test_a_model_that_never_answers_is_abandoned_within_the_bound() -> None:
    drafter = StandInDrafter([None])
    started = asyncio.get_running_loop().time()

    with capture_logs() as events:
        summary = await summarised(drafter, bound=timedelta(seconds=0.05))

    assert asyncio.get_running_loop().time() - started < 1
    assert summary == fallback_summary(ended(SAID), locale="en")
    assert drafter.abandoned == 1
    assert [event["failure"] for event in events] == ["TimeoutError"]


class TestARefusedDraft:
    @pytest.mark.usefixtures("unfiltered_logging")
    async def test_is_asked_for_again_with_why_and_the_correction_kept(self) -> None:
        drafter = StandInDrafter([COPIED, GOOD])
        metrics = RecordingMetrics()

        with capture_logs() as events:
            summary = await summarised(drafter, metrics=metrics)

        assert summary.headline == HEADLINE
        assert summary.details == (
            ExtractedDetail("address", "14 Alder Close", "the parcel is at 14 Alder Close"),
        )
        [(first, _), (again, correction)] = drafter.asked
        # The same call, with the refused draft and exactly why it was refused.
        assert again == first
        assert correction == DraftCorrection(COPIED, (DraftProblem.RESTATES_THE_CALL,))
        assert written(metrics) == ["corrected_draft"]
        assert events == [
            {
                "event": "summary.draft_refused",
                "call_id": "call-1",
                "draft": "first_draft",
                "problems": ["restates_the_call"],
                "log_level": "warning",
            }
        ]

    @pytest.mark.usefixtures("unfiltered_logging")
    async def test_whose_correction_is_refused_too_is_the_fallback_logged_by_problem(self) -> None:
        drafter = StandInDrafter([INVENTED, INVENTED, GOOD])
        metrics = RecordingMetrics()

        with capture_logs() as events:
            summary = await summarised(drafter, metrics=metrics)

        assert summary == fallback_summary(ended(SAID), locale="en")
        assert len(drafter.asked) == 2
        assert written(metrics) == ["fallback"]
        assert events == [
            {
                "event": "summary.draft_refused",
                "call_id": "call-1",
                "draft": draft,
                "problems": ["wrong_outcome", "ungrounded_detail"],
                "log_level": "warning",
            }
            for draft in ("first_draft", "corrected_draft")
        ]
        assert "walrus" not in repr(events)

    @pytest.mark.usefixtures("unfiltered_logging")
    async def test_whose_correction_fails_is_the_fallback_and_not_asked_again(self) -> None:
        drafter = StandInDrafter([COPIED, SummaryNotWrittenError(SECRET), GOOD])
        metrics = RecordingMetrics()

        with capture_logs() as events:
            summary = await summarised(drafter, metrics=metrics)

        assert summary == fallback_summary(ended(SAID), locale="en")
        assert len(drafter.asked) == 2
        assert written(metrics) == ["fallback"]
        assert [event["event"] for event in events] == [
            "summary.draft_refused",
            "summary.model_failed",
        ]
        assert "walrus" not in repr(events)

    @pytest.mark.usefixtures("unfiltered_logging")
    async def test_is_not_corrected_with_less_time_left_than_the_draft_took(self) -> None:
        # Three of five seconds spent on the draft leaves two for a correction that takes three.
        drafter = StandInDrafter([COPIED, GOOD], takes=3)
        metrics = RecordingMetrics()

        with capture_logs() as events:
            summary = await summarised(drafter, bound=timedelta(seconds=5), metrics=metrics)

        assert summary == fallback_summary(ended(SAID), locale="en")
        assert len(drafter.asked) == 1
        assert written(metrics) == ["fallback"]
        assert events[-1] == {
            "event": "summary.correction_skipped",
            "call_id": "call-1",
            "log_level": "warning",
        }

    async def test_is_corrected_while_as_much_time_is_left_as_the_draft_took(self) -> None:
        drafter = StandInDrafter([COPIED, GOOD], takes=2.5)

        summary = await summarised(drafter, bound=timedelta(seconds=5))

        assert summary.headline == HEADLINE
        assert len(drafter.asked) == 2

    @pytest.mark.usefixtures("unfiltered_logging")
    async def test_whose_correction_never_answers_is_abandoned_within_the_same_bound(self) -> None:
        drafter = StandInDrafter([COPIED, None])
        metrics = RecordingMetrics()
        started = asyncio.get_running_loop().time()

        with capture_logs() as events:
            summary = await summarised(drafter, bound=timedelta(seconds=0.05), metrics=metrics)

        assert asyncio.get_running_loop().time() - started < 1
        assert summary == fallback_summary(ended(SAID), locale="en")
        assert drafter.abandoned == 1
        assert written(metrics) == ["fallback"]
        assert events[-1]["failure"] == "TimeoutError"


async def test_cancelling_the_summary_is_not_mistaken_for_a_failed_model() -> None:
    # Cancellation is not a model failure and is not swallowed.
    drafter = StandInDrafter([None])
    metrics = RecordingMetrics()
    task = asyncio.create_task(summarised(drafter, metrics=metrics))
    await drafter.waiting.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert drafter.abandoned == 1
    assert written(metrics) == []


async def test_a_call_still_in_progress_is_a_defect_not_a_fallback() -> None:
    live = CallSession(
        id=CallId("call-1"), user_id=UserId("user-1"), caller=STRANGER, started_at=START
    )
    summariser = ModelCallSummariser(
        StandInDrafter([GOOD]), timeout=BOUND, metrics=RecordingMetrics()
    )

    with pytest.raises(InvariantError, match="ended"):
        await summariser.summarise(CallFacts(live), ended(SAID).call.transcript, locale="en")


@pytest.mark.parametrize("seconds", [0, -1])
def test_a_summary_with_no_time_to_be_written_in_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="time"):
        ModelCallSummariser(
            StandInDrafter([GOOD]), timeout=timedelta(seconds=seconds), metrics=RecordingMetrics()
        )
