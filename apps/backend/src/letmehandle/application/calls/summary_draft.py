"""What a model is asked to summarise, and the draft it writes before any check."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letmehandle.application.calls.summary_checks import DraftProblem
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.intent import CallIntent
    from letmehandle.domain.models.summary import CallOutcome, CallSummary


class DetailKind(StrEnum):
    """The kinds of detail a summary keeps, stored as an `ExtractedDetail`'s label."""

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
    """What a model wrote; its `outcome` is only compared with the facts, never kept."""

    headline: str
    intent: CallIntent
    outcome: CallOutcome
    details: tuple[DraftDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryRequest:
    """What a model is given for one ended call; `known` is the summary the facts alone support."""

    known: CallSummary
    transcript: tuple[TranscriptEntry, ...]
    locale: str


@dataclass(frozen=True, slots=True)
class DraftCorrection:
    """A refused draft and the problems found with it, for the model to correct."""

    refused: SummaryDraft
    problems: tuple[DraftProblem, ...]
