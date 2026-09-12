"""Accepting what a handset reports about its own calls.

Three rules, each the reason this is a service rather than a route writing rows:

  A report is scoped to the user it arrived from. The handset's identifiers are only unique
  within that account, so the event the product sees carries identifiers that name the user as
  well — otherwise one account's handset could speak for another account's call by guessing an
  identifier.

  A repeat is inert. Handsets resend what they have not seen acknowledged, and a report stored
  once is acknowledged every time it arrives without being handed on again.

  A late report is stored and not replayed. A call already reported as over does not come back
  to life because its "answered" arrived after its "ended"; order is resolved by state, not by
  arrival.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.ports.reported_calls import (
        CallEventSink,
        CallReport,
        CallReportRepository,
    )


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """What became of a batch, by the handset's own event identifiers.

    Both lists are safe for the handset to forget: a duplicate was already stored.
    """

    accepted: tuple[EventId, ...]
    duplicates: tuple[EventId, ...]


def scoped_call_id(user_id: UserId, call_id: CallId) -> CallId:
    """The call as the rest of the product knows it: this user's call with this identifier."""
    return CallId(f"{user_id.value}:{call_id.value}")


def scoped_event_id(user_id: UserId, event_id: EventId) -> EventId:
    """The event as the rest of the product knows it, for the same reason as the call."""
    return EventId(f"{user_id.value}:{event_id.value}")


class CallReporting:
    """Store a handset's reports and hand on the ones that are new and still current."""

    def __init__(self, reports: CallReportRepository, sink: CallEventSink) -> None:
        self._reports = reports
        self._sink = sink

    async def report(self, user_id: UserId, batch: Sequence[CallReport]) -> ReportOutcome:
        accepted: list[EventId] = []
        duplicates: list[EventId] = []
        for report in batch:
            # Asked before recording, so that the report being recorded is not what answers it.
            superseded = report.kind is not CallEventKind.ENDED and await self._reports.has_ended(
                user_id, report.call_id
            )
            if not await self._reports.record(user_id, report):
                duplicates.append(report.event_id)
                continue
            accepted.append(report.event_id)
            if not superseded:
                await self._sink.publish(user_id, _to_event(user_id, report))
        return ReportOutcome(accepted=tuple(accepted), duplicates=tuple(duplicates))


def _to_event(user_id: UserId, report: CallReport) -> CallEvent:
    return CallEvent(
        kind=report.kind,
        call_id=scoped_call_id(user_id, report.call_id),
        event_id=scoped_event_id(user_id, report.event_id),
        # Only the number. A handset that is not the phone app is not given the caller's
        # contact name, and a category would be a guess: the handset does not classify.
        caller=None if report.caller_number is None else Caller(number=report.caller_number),
        detail=None if report.ending is None else report.ending.value,
        screening=report.screening,
    )
