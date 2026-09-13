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

Neither port here names a framework or a model (D-026). The model runs in an adapter behind
`SummaryDrafter`, and everything that decides whether its draft is kept lives in this layer.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summary_checks import problems_with
from letmehandle.domain.models.summary import ExtractedDetail
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.intent import CallIntent
    from letmehandle.domain.models.summary import CallOutcome, CallSummary


class DetailKind(StrEnum):
    """The kinds of detail a summary keeps, which are the ones a user acts on.

    Stored as an `ExtractedDetail`'s label. A commitment is split by which way it went, because
    "they will call back" and "they would not refund it" send the user in opposite directions.
    """

    TIME = "time"
    NAME = "name"
    REFERENCE_NUMBER = "reference_number"
    ADDRESS = "address"
    AMOUNT = "amount"
    COMMITMENT_MADE = "commitment_made"
    COMMITMENT_DECLINED = "commitment_declined"


@dataclass(frozen=True, slots=True)
class DraftDetail:
    """One detail a model says the call contained, and the words it says it came from."""

    kind: DetailKind
    value: str
    evidence: str


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """What a model wrote, before anything has checked it.

    `outcome` is the model's reading of how the call ended. It is never used as the outcome, which
    the facts settle; a draft that disagrees with them misread the call and is refused.
    """

    headline: str
    intent: CallIntent
    outcome: CallOutcome
    details: tuple[DraftDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """What a model is given to summarise one ended call.

    `known` is the summary the facts alone support. The model reads its outcome and who called
    from it, and a kept draft changes only its headline, intent and details.
    """

    known: CallSummary
    transcript: tuple[TranscriptEntry, ...]
    locale: str


class SummaryNotWrittenError(Exception):
    """The model finished without writing a summary in the shape asked for."""


class SummaryDrafter(ABC):
    """Asks a model for a draft summary. Implemented by an adapter."""

    @abstractmethod
    async def draft(self, request: SummaryRequest) -> SummaryDraft:
        """The model's draft of `request`.

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
    """Keeps a model's draft when every check passes it, and the fallback otherwise."""

    def __init__(self, drafter: SummaryDrafter, *, timeout: timedelta) -> None:
        if timeout.total_seconds() <= 0:
            raise ValueError("a summary needs time to be written in")
        self._drafter = drafter
        self._timeout = timeout
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
                draft = await self._drafter.draft(request)
        # Deliberately broad, for the reason the call agent gives: the port promises a summary
        # whatever the model does. Logged by kind and never by content, which is somebody's call.
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "summary.model_failed", call_id=str(known.call_id), failure=type(error).__name__
            )
            return known

        problems = problems_with(draft, request)
        if problems:
            self._logger.warning(
                "summary.draft_refused",
                call_id=str(known.call_id),
                problems=[problem.value for problem in problems],
            )
            return known
        return _kept(known, draft)


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
