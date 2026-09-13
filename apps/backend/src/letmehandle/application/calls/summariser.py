"""The summary a model writes of a call that has ended, and the facts it may not contradict.

When a call ends, orchestration hands `CallSummariser` what it knows for certain and what was said,
and gets back a summary that is always valid. A model writes the parts only a reading of the call
can supply — the sentence the user reads, what the call was for, the details worth keeping — and
nothing else: the outcome, the timings, who was on the call and why the user was asked for are the
facts' own, taken from the fallback summary those facts build.

Whatever the model does, the caller of this port gets a summary. A model that is down, slow, or
answers with something the checks refuse produces the fallback (D-014): the summary is the only
record left once the transcript is purged, and a call missing from history is worse than a plain
sentence about it.

A draft the checks refuse gets one correction first: the model is asked again with the problems the
checks named, and a corrected draft that passes is kept. Most refusals are a model near the
mark — a clause of the call repeated, a detail reworded — and those it can fix when told. A model
that failed, timed out or wrote nothing usable is not asked again, because nothing it was told
would change that. The correction is inside the same bound as the draft, and is not started once
there is less time left than the draft took, since a correction abandoned half-written is the
fallback at a model's price.

Neither port here names a framework or a model (D-026). The model runs in an adapter behind
`SummaryDrafter`, and everything that decides whether its draft is kept lives in this layer.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summary_checks import problems_with
from letmehandle.application.calls.summary_draft import (
    DraftCorrection,
    SummaryDraft,
    SummaryRequest,
)
from letmehandle.domain.models.summary import ExtractedDetail
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import timedelta

    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.application.calls.summary_checks import DraftProblem
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.metrics import MetricsRecorder


class Written(StrEnum):
    """Which summary a call a model was asked about ended up with."""

    FIRST_DRAFT = "first_draft"
    CORRECTED_DRAFT = "corrected_draft"
    FALLBACK = "fallback"


# Counted once per call a model was asked to summarise, so the share kept on the first draft, kept
# after a correction and fallen back shows whether corrections earn the time they take.
SUMMARY_WRITTEN: Final = catalogue.count("call.summary_written", outcome=Written)


class SummaryNotWrittenError(Exception):
    """The model finished without writing a summary in the shape asked for."""


class SummaryDrafter(ABC):
    """Asks a model for a draft summary. Implemented by an adapter."""

    @abstractmethod
    async def draft(
        self, request: SummaryRequest, correction: DraftCorrection | None = None
    ) -> SummaryDraft:
        """The model's draft of `request`, or with `correction`, its draft again once refused.

        May raise anything a model and its client raise; `CallSummariser` absorbs it.
        """


class CallSummariser(ABC):
    """Writes the summary of a call that has ended. What orchestration calls at teardown."""

    @abstractmethod
    async def summarise(
        self, facts: CallFacts, transcript: Sequence[TranscriptEntry], *, locale: str
    ) -> CallSummary:
        """A valid summary of the call, never a failure.

        Raises only `InvariantError` for a call still in progress, as `fallback_summary` does,
        because that is a defect in whoever asked rather than a model misbehaving.
        """


class ModelCallSummariser(CallSummariser):
    """Keeps a model's draft when every check passes it, a corrected one next, the fallback last.

    `clock` is monotonic seconds, read to decide whether a correction still has time to finish;
    the bound itself is enforced by the event loop whatever the clock says.
    """

    def __init__(
        self,
        drafter: SummaryDrafter,
        *,
        timeout: timedelta,
        metrics: MetricsRecorder,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if timeout.total_seconds() <= 0:
            raise ValueError("a summary needs time to be written in")
        self._drafter = drafter
        self._timeout = timeout
        self._metrics = metrics
        self._clock = clock
        self._logger = get_logger(__name__)

    async def summarise(
        self, facts: CallFacts, transcript: Sequence[TranscriptEntry], *, locale: str
    ) -> CallSummary:
        """Ask the model within the bound, check what it wrote, and fall back on any failure."""
        known = fallback_summary(facts, locale=locale)
        if not transcript:
            # Nothing was said, so there is nothing a model could add that would not be invented.
            return known
        request = SummaryRequest(known=known, transcript=tuple(transcript), locale=locale)
        try:
            async with asyncio.timeout(self._timeout.total_seconds()):
                summary, written = await self._written(request)
        # Deliberately broad, for the reason the call agent gives: the port promises a summary
        # whatever the model does. Logged by kind and never by content, which is somebody's call.
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "summary.model_failed", call_id=str(known.call_id), failure=type(error).__name__
            )
            summary, written = known, Written.FALLBACK
        # Counted outside the model's failures, so a metric refused is a defect that surfaces.
        self._metrics.increment(SUMMARY_WRITTEN, {"outcome": written})
        return summary

    async def _written(self, request: SummaryRequest) -> tuple[CallSummary, Written]:
        """The draft if it passes, else one correction if there is time for it, else the facts."""
        started = self._clock()
        draft = await self._drafter.draft(request)
        problems = self._refusals(draft, request, Written.FIRST_DRAFT)
        if not problems:
            return _kept(request.known, draft), Written.FIRST_DRAFT
        if not self._time_for_a_correction(started):
            self._logger.warning("summary.correction_skipped", call_id=str(request.known.call_id))
            return request.known, Written.FALLBACK
        corrected = await self._drafter.draft(request, DraftCorrection(draft, problems))
        if self._refusals(corrected, request, Written.CORRECTED_DRAFT):
            return request.known, Written.FALLBACK
        return _kept(request.known, corrected), Written.CORRECTED_DRAFT

    def _refusals(
        self, draft: SummaryDraft, request: SummaryRequest, which: Written
    ) -> tuple[DraftProblem, ...]:
        """Why the checks refuse `draft`, logged by problem when they do."""
        problems = problems_with(draft, request)
        if problems:
            self._logger.warning(
                "summary.draft_refused",
                call_id=str(request.known.call_id),
                draft=which.value,
                problems=[problem.value for problem in problems],
            )
        return problems

    def _time_for_a_correction(self, started: float) -> bool:
        # A correction is the same request and a little more, so it takes about as long as the
        # draft did; one started with less time left than that is likely abandoned half-written.
        spent = self._clock() - started
        return self._timeout.total_seconds() - spent >= spent


def _kept(known: CallSummary, draft: SummaryDraft) -> CallSummary:
    """`known`, with what the checked draft adds. The checks refuse all the summary refuses."""
    return replace(
        known,
        headline=draft.headline,
        intent=draft.intent,
        details=tuple(
            ExtractedDetail(label=each.kind.value, value=each.value, evidence=each.evidence)
            for each in draft.details
        ),
    )
