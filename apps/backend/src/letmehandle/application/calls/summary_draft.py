"""What a model is asked to summarise, and what it drafts, before anything has checked it.

Kept apart from the summariser so that the checks, the prompts and the adapter can name these
shapes without importing the port that uses them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
