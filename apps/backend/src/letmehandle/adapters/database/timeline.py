"""Call timelines, and the outline diagnostics reads of a call, implemented against PostgreSQL.

Apart from the other call repositories because it holds no cipher and needs none: it reads only the
columns that are structure, and never the sealed caller, transcript or summary beside them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from letmehandle.domain.models.call import CallHandling, Participant, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationStatus, NotificationDelivery
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.models.timeline import (
    CallOutline,
    EscalationOutline,
    MarkKind,
    TimelineMark,
)
from letmehandle.domain.ports.repositories import CallTimelineRepository

from .models import (
    CallParticipantRow,
    CallRow,
    CallSummaryRow,
    CallTimelineMarkRow,
    EscalationContextRow,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.models.identifiers import CallId


class SqlCallTimelineRepository(CallTimelineRepository):
    """Marks in insertion order, which is the order the call's one run made them."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, call_id: CallId, marks: Sequence[TimelineMark]) -> None:
        if not marks:
            return
        await self._session.execute(
            insert(CallTimelineMarkRow).values(
                [
                    {
                        "call_id": call_id.value,
                        "at": mark.at,
                        "kind": mark.kind.value,
                        "name": mark.name,
                    }
                    for mark in marks
                ]
            )
        )
        await self._session.flush()

    async def outline(self, call_id: CallId) -> CallOutline | None:
        # Only the columns that are structure are selected, so nothing sealed is even fetched.
        call = (
            await self._session.execute(
                select(
                    CallRow.state,
                    CallRow.handling,
                    CallRow.started_at,
                    CallRow.ended_at,
                    CallRow.escalated_at,
                ).where(CallRow.id == call_id.value)
            )
        ).one_or_none()
        if call is None:
            return None
        return CallOutline(
            call_id=call_id,
            state=CallState(call.state),
            handling=None if call.handling is None else CallHandling(call.handling),
            started_at=call.started_at,
            ended_at=call.ended_at,
            escalated_at=call.escalated_at,
            participants=await self._participants(call_id),
            escalation=await self._escalation(call_id),
            outcome=await self._outcome(call_id),
            marks=await self._marks(call_id),
        )

    async def _participants(self, call_id: CallId) -> tuple[Participant, ...]:
        rows = await self._session.execute(
            select(
                CallParticipantRow.role, CallParticipantRow.joined_at, CallParticipantRow.left_at
            )
            .where(CallParticipantRow.call_id == call_id.value)
            .order_by(CallParticipantRow.position)
        )
        return tuple(
            Participant(ParticipantRole(row.role), row.joined_at, row.left_at) for row in rows
        )

    async def _escalation(self, call_id: CallId) -> EscalationOutline | None:
        row = (
            await self._session.execute(
                select(
                    EscalationContextRow.reason,
                    EscalationContextRow.status,
                    EscalationContextRow.delivery,
                ).where(EscalationContextRow.call_id == call_id.value)
            )
        ).one_or_none()
        if row is None:
            return None
        return EscalationOutline(
            reason=EscalationReason(row.reason),
            status=EscalationStatus(row.status),
            delivery=NotificationDelivery(row.delivery),
        )

    async def _outcome(self, call_id: CallId) -> CallOutcome | None:
        outcome = await self._session.scalar(
            select(CallSummaryRow.outcome).where(CallSummaryRow.call_id == call_id.value)
        )
        return None if outcome is None else CallOutcome(outcome)

    async def _marks(self, call_id: CallId) -> tuple[TimelineMark, ...]:
        rows = await self._session.execute(
            select(CallTimelineMarkRow.at, CallTimelineMarkRow.kind, CallTimelineMarkRow.name)
            .where(CallTimelineMarkRow.call_id == call_id.value)
            .order_by(CallTimelineMarkRow.id)
        )
        return tuple(TimelineMark(row.at, MarkKind(row.kind), row.name) for row in rows)
