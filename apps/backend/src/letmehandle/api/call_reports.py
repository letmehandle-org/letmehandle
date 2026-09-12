"""A handset reporting its own calls.

The handset decides a call before it rings and cannot wait for this service to do it; it tells
this service what happened afterwards. The route belongs to the signed-in user: the reports are
stored against that user and no other, so a handset can only ever speak for its own account's
calls, whatever identifiers it sends.
"""

from __future__ import annotations

from fastapi import APIRouter

from letmehandle.api.body_limit import limited_body_route
from letmehandle.api.call_report_schemas import (
    CallReportBatch,
    CallReportPayload,
    CallReportReceipt,
    ReportedCallKind,
)
from letmehandle.api.dependencies import CallReports, CurrentUser
from letmehandle.api.errors import UNPROCESSABLE, ApiError
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.domain.ports.reported_calls import CallReport

# The largest request body read: a full batch of the largest reports is a fraction of this.
REPORTS_BODY_LIMIT_BYTES = 256 * 1024

router = APIRouter(
    prefix="/v1", tags=["calls"], route_class=limited_body_route(REPORTS_BODY_LIMIT_BYTES)
)

_KINDS = {
    ReportedCallKind.INCOMING: CallEventKind.INCOMING,
    ReportedCallKind.ANSWERED: CallEventKind.ANSWERED,
    ReportedCallKind.ENDED: CallEventKind.ENDED,
}


@router.post(
    "/calls/reports",
    response_model=CallReportReceipt,
    summary="Report what happened to this handset's calls",
)
async def report_calls(
    body: CallReportBatch, user: CurrentUser, reporting: CallReports
) -> CallReportReceipt:
    try:
        batch = [_to_report(payload) for payload in body.reports]
    except InvariantError as error:
        # A screening decision on an ended call, or an ending on an incoming one: the handset
        # sent something that cannot have happened, which is the request's problem.
        raise ApiError(UNPROCESSABLE, "invalid_request", str(error)) from error
    outcome = await reporting.report(user.id, batch)
    return CallReportReceipt(
        accepted=[event.value for event in outcome.accepted],
        duplicates=[event.value for event in outcome.duplicates],
    )


def _to_report(payload: CallReportPayload) -> CallReport:
    return CallReport(
        event_id=EventId(payload.event_id),
        call_id=CallId(payload.call_id),
        kind=_KINDS[payload.kind],
        occurred_at=payload.occurred_at,
        caller_number=(
            None if payload.caller_number is None else PhoneNumber(payload.caller_number)
        ),
        screening=payload.screening,
        ending=payload.ending,
    )
