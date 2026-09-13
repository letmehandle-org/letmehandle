"""Call history, over HTTP.

Every route is the signed-in user's own calls and nobody else's. A call belonging to somebody
else is `404 call_not_found`, byte for byte the response for a call that never existed, so an
identifier guessed or leaked tells its holder nothing (D-012).

The error codes a client branches on, stated once:

  `404 call_not_found`           no such call for this user, whether or not it is somebody else's.
  `404 transcript_not_recorded`  the call exists and nothing was ever said on it — rejected, put
                                 straight through, or not yet answered. There never will be one
                                 to read, unless the call is still going.
  `410 transcript_purged`        there was a transcript and the user's retention has deleted it.
                                 The summary remains; the words do not, and will not come back.
  `422 invalid_cursor`           the cursor is not one this API issued. Start again from the first
                                 page.
  `422 invalid_request`          a malformed filter: a bound with no timezone, a range that ends
                                 before it starts, a limit outside 1 to 100.
  `503 call_history_unavailable` this deployment has no transcript keys, so nothing can be read.

Deleting is `204` whether or not there was anything to delete. A second delete succeeding is
what makes a retry safe, and a delete of somebody else's call answering like one of your own is
what keeps it from being a way to test which identifiers exist.

Nothing here logs what a transcript, a summary or a caller says. The only lines these routes
write are the ones every request writes.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from pydantic import AwareDatetime

from letmehandle.api.call_schemas import (
    CallDetailResponse,
    CallerPayload,
    CallListItem,
    CallPageResponse,
    CallStatus,
    CallTimingsPayload,
    ExtractedDetailPayload,
    TranscriptLinePayload,
    TranscriptResponse,
)
from letmehandle.api.dependencies import CallHistory, CurrentUser
from letmehandle.api.errors import UNPROCESSABLE, ApiError, invalid_request
from letmehandle.application.calls.history import CallRecord
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.ports.repositories import (
    MAX_CALL_PAGE,
    CallCursor,
    CallFilter,
    TranscriptStatus,
)

router = APIRouter(prefix="/v1", tags=["calls"])

DEFAULT_PAGE_SIZE = 20

# Room for a timestamp and the longest identifier, encoded, and not much more: a cursor is echoed
# back verbatim, and an unbounded one is an unbounded thing to decode.
_MAX_CURSOR_LENGTH = 256


@router.get("/calls", response_model=CallPageResponse, summary="List calls")
async def list_calls(
    user: CurrentUser,
    history: CallHistory,
    limit: Annotated[int, Query(ge=1, le=MAX_CALL_PAGE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=_MAX_CURSOR_LENGTH)] = None,
    outcome: CallOutcome | None = None,
    started_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    started_before: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    human_joined: bool | None = None,
) -> CallPageResponse:
    """The user's calls, newest first.

    `from` is inclusive and `to` exclusive, both on when the call started, and both must carry a
    timezone offset. `outcome` and `human_joined` match only calls that have a summary. Calls still
    in progress are listed, with `status` `in_progress` and no outcome yet.
    """
    try:
        matching = CallFilter(
            outcome=outcome,
            started_from=started_from,
            started_before=started_before,
            human_joined=human_joined,
        )
    except InvariantError as error:
        raise invalid_request(error) from error
    page = await history.page(
        user.id,
        limit=limit,
        after=None if cursor is None else _decode_cursor(cursor),
        matching=matching,
    )
    return CallPageResponse(
        calls=[_list_item(record) for record in page.records],
        next_cursor=None if page.next_cursor is None else _encode_cursor(page.next_cursor),
    )


@router.get("/calls/{call_id}", response_model=CallDetailResponse, summary="Open a call")
async def read_call(call_id: str, user: CurrentUser, history: CallHistory) -> CallDetailResponse:
    """One call in full: its summary, its timeline, and whether its transcript can still be read."""
    detail = await history.detail(user.id, _call_id(call_id))
    if detail is None:
        raise _call_not_found()
    record = detail.record
    summary = record.summary
    return CallDetailResponse(
        **_list_item(record).model_dump(),
        handling=record.call.handling,
        intent=None if summary is None else summary.intent,
        importance=None if summary is None else summary.importance,
        escalation_reason=None if summary is None else summary.escalation_reason,
        timings=CallTimingsPayload(
            received_at=record.call.started_at,
            answered_at=record.answered_at,
            escalated_at=record.call.escalated_at,
            human_joined_at=record.human_joined_at,
            ended_at=record.call.ended_at,
        ),
        details=[
            ExtractedDetailPayload(label=each.label, value=each.value, evidence=each.evidence)
            for each in (() if summary is None else summary.details)
        ],
        transcript_available=detail.transcript is TranscriptStatus.RETAINED,
        transcript_retention_days=detail.retention.days,
        transcript_expires_at=detail.transcript_expires_at,
    )


@router.get(
    "/calls/{call_id}/transcript",
    response_model=TranscriptResponse,
    summary="Read a call's transcript",
)
async def read_transcript(
    call_id: str, user: CurrentUser, history: CallHistory, response: Response
) -> TranscriptResponse:
    """What was said, while the user's retention still keeps it.

    `410 transcript_purged` once retention has deleted it, `404 transcript_not_recorded` for a
    call nothing was said on, `404 call_not_found` for no such call.
    """
    view = await history.transcript(user.id, _call_id(call_id))
    if view is None:
        raise _call_not_found()
    if view.status is TranscriptStatus.PURGED:
        raise ApiError(
            status.HTTP_410_GONE,
            "transcript_purged",
            "This call's transcript was deleted under your retention setting. Its summary is kept.",
        )
    if view.status is TranscriptStatus.NOT_RECORDED:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "transcript_not_recorded",
            "Nothing was said on this call, so it has no transcript.",
        )
    # Kept out of every cache between here and the phone: a shared proxy holding a copy would be a
    # transcript the purge never reaches.
    response.headers["Cache-Control"] = "no-store"
    return TranscriptResponse(
        call_id=view.call.id.value,
        entries=[
            TranscriptLinePayload(speaker=entry.speaker, text=entry.text, said_at=entry.at_instant)
            for entry in view.entries
        ],
        transcript_retention_days=view.retention.days,
        transcript_expires_at=view.retention.expires_at(view.call),
    )


@router.delete("/calls/{call_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a call")
async def delete_call(call_id: str, user: CurrentUser, history: CallHistory) -> Response:
    """Delete the call, its transcript and its summary, immediately. `204` even if already gone."""
    try:
        identifier = CallId(call_id)
    except InvariantError:
        # No call can have this identifier, so there is nothing to delete — which is a success.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    await history.delete(user.id, identifier)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- mapping


def _list_item(record: CallRecord) -> CallListItem:
    summary = record.summary
    caller = record.caller
    return CallListItem(
        id=record.call.id.value,
        started_at=record.call.started_at,
        status=CallStatus.ENDED if record.call.is_over else CallStatus.IN_PROGRESS,
        caller=CallerPayload(
            category=caller.category,
            display_name=record.display_name,
            number_withheld=caller.is_anonymous,
        ),
        outcome=None if summary is None else summary.outcome,
        headline=None if summary is None else summary.headline,
        human_joined=record.human_joined_at is not None,
        duration_seconds=record.duration_seconds,
    )


def _call_id(value: str) -> CallId:
    """The identifier, or the answer for a call that does not exist — which no such value can."""
    try:
        return CallId(value)
    except InvariantError as error:
        raise _call_not_found() from error


def _call_not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "call_not_found", "There is no such call.")


def _encode_cursor(cursor: CallCursor) -> str:
    """Opaque to a client, and nothing more than where the last page ended.

    Not signed, because nothing in it needs protecting: it holds the start and identifier of a
    call the client was just shown, and a cursor edited to point anywhere else still only pages
    through the user's own calls.
    """
    raw = json.dumps([cursor.started_at.isoformat(), cursor.call_id.value]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(text: str) -> CallCursor:
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        started_at, call_id = json.loads(raw)
        cursor = CallCursor(datetime.fromisoformat(started_at), CallId(call_id))
    except (binascii.Error, ValueError, TypeError, InvariantError) as error:
        raise _invalid_cursor() from error
    if cursor.started_at.tzinfo is None:
        raise _invalid_cursor()
    return cursor


def _invalid_cursor() -> ApiError:
    return ApiError(
        UNPROCESSABLE,
        "invalid_cursor",
        "This cursor was not issued here. Start from the first page.",
    )
