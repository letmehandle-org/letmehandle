"""A handset reporting its own calls.

The handset decides a call before it rings and cannot wait for this service to do it; it tells
this service what happened afterwards. The route belongs to the signed-in user: the reports are
stored against that user and no other, so a handset can only ever speak for its own account's
calls, whatever identifiers it sends.
"""

from __future__ import annotations

from fastapi import APIRouter

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
from letmehandle.api.call_report_schemas import (
    CallReportBatch,
    CallReportPayload,
    CallReportReceipt,
    RejectedReport,
    ReportedCallKind,
    UnreadableReport,
)
from letmehandle.api.dependencies import CallReports, CurrentUser
from letmehandle.api.errors import rate_limited
from letmehandle.application.calls.reports import ReportingRateLimitedError
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.domain.ports.reported_calls import CallReport

router = APIRouter(
    prefix="/v1", tags=["calls"], route_class=limited_body_route(JSON_BODY_LIMIT_BYTES)
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
    batch: list[CallReport] = []
    rejected: list[RejectedReport] = []
    for index, payload in enumerate(body.readings()):
        if isinstance(payload, UnreadableReport):
            rejected.append(
                RejectedReport(index=index, event_id=payload.event_id, reason=payload.reason)
            )
            continue
        try:
            batch.append(_to_report(payload))
        except InvariantError as error:
            # A screening decision on an ended call, or an ending on an incoming one: the handset
            # reported something that cannot have happened, and that report alone is refused.
            rejected.append(
                RejectedReport(index=index, event_id=payload.event_id, reason=str(error))
            )
    try:
        outcome = await reporting.report(user.id, batch)
    except ReportingRateLimitedError as error:
        raise rate_limited(
            error.retry_after_seconds, "Too many reports. Try again shortly."
        ) from error
    return CallReportReceipt(
        accepted=[event.value for event in outcome.accepted],
        duplicates=[event.value for event in outcome.duplicates],
        rejected=rejected,
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
