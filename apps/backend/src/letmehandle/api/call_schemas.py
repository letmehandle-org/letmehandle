"""What the call history API returns.

Separate from the domain types, as everywhere in this layer. Only responses live here: the one
thing a client sends is a filter, and a filter is query parameters.

Who called is a category, a name only for somebody the user knows, and whether the number was
withheld — never the number itself. See `application/calls/history.py` for why.
"""

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
    """Whether a call is still going. An ended call's summary can arrive a moment after it ends."""

    IN_PROGRESS = "in_progress"
    ENDED = "ended"


class CallerPayload(Response):
    category: CallerCategory
    # Only for a caller the user has told us about; a stranger's is null whatever the network sent.
    display_name: str | None
    number_withheld: bool


class CallListItem(Response):
    """One call in the list.

    `outcome` and `headline` are null until the call has a summary: while it is in progress, and
    for the moment between its end and its summary being written.
    """

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
    # When the assistant first asked for the user, whether or not they were reached.
    escalated_at: datetime | None
    human_joined_at: datetime | None
    ended_at: datetime | None


class ExtractedDetailPayload(Response):
    label: str
    value: str
    evidence: str | None


class CallDetailResponse(CallListItem):
    """One call in full.

    `transcript_retention_days` is the user's setting as it stands, which is the one the purge
    applies. `transcript_expires_at` is when the last of the transcript is due to go — null when
    there is none to go, or while the call is still going.
    """

    # Whom routing gave the call to: straight through to the user, or the assistant. Null for a
    # call rejected by the rules, and for one still being routed.
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
