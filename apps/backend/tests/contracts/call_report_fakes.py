"""In-memory report storage and event forwarding; a report is kept once per user and event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.domain.ports.reported_calls import CallEventSink, CallReportRepository

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.ports.call_transport import CallEvent
    from letmehandle.domain.ports.reported_calls import CallReport


class InMemoryCallReportRepository(CallReportRepository):
    def __init__(self) -> None:
        self.rows: dict[tuple[UserId, str], CallReport] = {}

    async def record(self, user_id: UserId, report: CallReport) -> bool:
        key = (user_id, report.event_id.value)
        if key in self.rows:
            return False
        self.rows[key] = report
        return True

    async def has_ended(self, user_id: UserId, call_id: CallId) -> bool:
        return any(
            owner == user_id and report.call_id == call_id and report.kind is CallEventKind.ENDED
            for (owner, _), report in self.rows.items()
        )


class RecordingCallEventSink(CallEventSink):
    def __init__(self) -> None:
        self.published: list[CallEvent] = []

    async def publish(self, user_id: UserId, event: CallEvent) -> None:
        self.published.append(event)
