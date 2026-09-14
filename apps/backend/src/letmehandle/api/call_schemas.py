"""What the call history API returns; who called never includes the number."""

from __future__ import annotations

# Not moved into a type-checking block: pydantic reads these annotations at run time.
from datetime import datetime
from enum import StrEnum

from letmehandle.api.schemas import Response
from letmehandle.domain.models.call import CallHandling, Speaker
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import CallOutcome


class CallStatus(StrEnum):
    """Whether a call is still going."""

    IN_PROGRESS = "in_progress"
    ENDED = "ended"


class CallerPayload(Response):
    category: CallerCategory
    # Null for a caller the user has not named.
    display_name: str | None
    number_withheld: bool


class CallListItem(Response):
    """One call in the list; `outcome` and `headline` are null until it has a summary."""

    id: str
    started_at: datetime
    status: CallStatus
    caller: CallerPayload
    outcome: CallOutcome | None
    headline: str | None
    human_joined: bool
    duration_seconds: float | None


class CallPageResponse(Response):
    calls: list[CallListItem]
    # Pass back as `cursor`, with the same filters, for the next page. Null on the last page.
    next_cursor: str | None


class CallTimingsPayload(Response):
    """When each thing happened. A step that never happened is null."""

    received_at: datetime
    answered_at: datetime | None
    # When the assistant first asked for the user, reached or not.
    escalated_at: datetime | None
    human_joined_at: datetime | None
    ended_at: datetime | None


class ExtractedDetailPayload(Response):
    label: str
    value: str
    evidence: str | None


class CallDetailResponse(CallListItem):
    """One call in full, with the user's current retention and when its transcript is due to go."""

    # Null for a call rejected by the rules or still being routed.
    handling: CallHandling | None
    intent: CallIntent | None
    importance: CallImportance | None
    escalation_reason: EscalationReason | None
    timings: CallTimingsPayload
    details: list[ExtractedDetailPayload]
    transcript_available: bool
    transcript_retention_days: int
    transcript_expires_at: datetime | None


class TranscriptLinePayload(Response):
    speaker: Speaker
    text: str
    said_at: datetime


class TranscriptResponse(Response):
    """What remains of a call's transcript, in the order it was said, and when it goes."""

    call_id: str
    entries: list[TranscriptLinePayload]
    transcript_retention_days: int
    transcript_expires_at: datetime | None
