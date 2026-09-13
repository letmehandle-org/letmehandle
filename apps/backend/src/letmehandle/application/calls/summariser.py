"""Summarises an ended call with a model, one correction and a fallback (D-014)."""

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


# Counted once per call a model was asked to summarise, by which summary was kept.
SUMMARY_WRITTEN: Final = catalogue.count("call.summary_written", outcome=Written)


class SummaryNotWrittenError(Exception):
    """The model finished without writing a summary in the shape asked for."""


class SummaryDrafter(ABC):
    """Asks a model for a draft summary. Implemented by an adapter."""

    @abstractmethod
    async def draft(
        self, request: SummaryRequest, correction: DraftCorrection | None = None
    ) -> SummaryDraft:
        """The model's draft of `request` or its correction; may raise anything."""


class CallSummariser(ABC):
    """Writes the summary of a call that has ended. What orchestration calls at teardown."""

    @abstractmethod
    async def summarise(
        self, facts: CallFacts, transcript: Sequence[TranscriptEntry], *, locale: str
    ) -> CallSummary:
        """A valid summary; raises `InvariantError` only for a call still in progress."""


class ModelCallSummariser(CallSummariser):
    """Keeps a checked draft, then a checked correction if time allows, then the fallback."""

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
            # Nothing was said, so a model has nothing to add.
            return known
        request = SummaryRequest(known=known, transcript=tuple(transcript), locale=locale)
        try:
            async with asyncio.timeout(self._timeout.total_seconds()):
                summary, written = await self._written(request)
        # Any model failure becomes the fallback, logged by kind and never by content.
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "summary.model_failed", call_id=str(known.call_id), failure=type(error).__name__
            )
            summary, written = known, Written.FALLBACK
        # Recorded outside the handler, so a refused metric surfaces as a defect.
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
        # A correction takes about as long as the draft, so it needs that much time left.
        spent = self._clock() - started
        return self._timeout.total_seconds() - spent >= spent


def _kept(known: CallSummary, draft: SummaryDraft) -> CallSummary:
    """`known`, with the headline, intent and details of a checked draft."""
    return replace(
        known,
        headline=draft.headline,
        intent=draft.intent,
        details=tuple(
            ExtractedDetail(label=each.kind.value, value=each.value, evidence=each.evidence)
            for each in draft.details
        ),
    )
