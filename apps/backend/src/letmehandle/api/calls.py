"""The signed-in user's call history, over HTTP; another user's call is not found (D-012)."""

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

# Room for an encoded timestamp and the longest identifier.
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
    """The user's calls, newest first; `from` inclusive and `to` exclusive, both with an offset."""
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
    """What was said, while the user's retention still keeps it."""
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
    # No cache may keep a copy the purge cannot reach.
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
    """The identifier, or the not-found answer for a value no call can have."""
    try:
        return CallId(value)
    except InvariantError as error:
        raise _call_not_found() from error


def _call_not_found() -> ApiError:
    return ApiError(status.HTTP_404_NOT_FOUND, "call_not_found", "There is no such call.")


def _encode_cursor(cursor: CallCursor) -> str:
    """Where the last page ended; unsigned, since any cursor pages only the user's own calls."""
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
