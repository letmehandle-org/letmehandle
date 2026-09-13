"""Call timelines through a real database: in order, with their call, and never content."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlEscalationContextRepository,
    SqlSummaryRepository,
)
from letmehandle.adapters.database.repositories import SqlUserRepository
from letmehandle.adapters.database.timeline import SqlCallTimelineRepository
from letmehandle.adapters.security.transcript_cipher import AesGcmTranscriptCipher
from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.domain.models.call import CallHandling, CallSession, Participant, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.models.timeline import (
    CallOutline,
    EscalationOutline,
    MarkKind,
    TimelineMark,
)
from letmehandle.domain.models.user import User
from tests.contracts.fakes import FixedClock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ME = UserId("user-1")
CALL = CallId("CAsim-timeline")
CIPHER = AesGcmTranscriptCipher([("key-a", bytes(range(32)))])


def later(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


async def a_stored_call(session: AsyncSession, *, ended: bool = False) -> CallSession:
    await SqlUserRepository(session, FixedClock(NOW)).add(
        User(id=ME, phone_number=PhoneNumber.parse("+12025550143"))
    )
    call = CallSession.restore(
        id=CALL,
        user_id=ME,
        caller=Caller(number=PhoneNumber.parse("+12025550199"), display_name="Parcel desk"),
        started_at=NOW,
        state=CallState.COMPLETED if ended else CallState.AGENT_HANDLING,
        participants=(Participant(ParticipantRole.AGENT, later(1), later(60) if ended else None),),
        ended_at=later(60) if ended else None,
        handling=CallHandling.ASSISTANT,
        escalated_at=later(5),
    )
    await SqlCallRepository(session, CIPHER, FixedClock(NOW)).save(call)
    return call


async def test_marks_are_read_back_in_the_order_they_were_written(session: AsyncSession) -> None:
    await a_stored_call(session)
    timeline = SqlCallTimelineRepository(session)
    first = (
        TimelineMark(NOW, MarkKind.TRANSITION, "received"),
        TimelineMark(later(1), MarkKind.TRANSITION, "routing"),
    )
    # Written later but stamped earlier, as a judgement's failure can be: order is writing order.
    second = (
        TimelineMark(later(3), MarkKind.TRANSITION, "agent_handling"),
        TimelineMark(later(2), MarkKind.FAILURE, "judgement.timeout"),
    )

    await timeline.append(CALL, first)
    await timeline.append(CALL, ())
    await timeline.append(CALL, second)

    outline = await timeline.outline(CALL)
    assert outline is not None
    assert outline.marks == first + second


async def test_the_outline_is_the_calls_structure_and_nothing_that_identifies_anybody(
    session: AsyncSession,
) -> None:
    call = await a_stored_call(session, ended=True)
    await SqlEscalationContextRepository(session, CIPHER).claim(
        ME,
        EscalationContext(
            call_id=CALL,
            reason=EscalationReason.CALLER_ASKED_FOR_THE_USER,
            raised_at=later(5),
            caller_label="Parcel desk",
            established="They are at the gate.",
            delivery=NotificationDelivery.DELIVERED,
        ),
    )
    await SqlSummaryRepository(session, CIPHER, FixedClock(NOW)).add(
        ME, fallback_summary(CallFacts(call), locale="en")
    )
    timeline = SqlCallTimelineRepository(session)
    await timeline.append(CALL, (TimelineMark(NOW, MarkKind.TRANSITION, "received"),))

    outline = await timeline.outline(CALL)

    assert outline == CallOutline(
        call_id=CALL,
        state=CallState.COMPLETED,
        handling=CallHandling.ASSISTANT,
        started_at=NOW,
        ended_at=later(60),
        escalated_at=later(5),
        participants=call.participants,
        escalation=EscalationOutline(
            reason=EscalationReason.CALLER_ASKED_FOR_THE_USER,
            status=EscalationStatus.LIVE,
            delivery=NotificationDelivery.DELIVERED,
        ),
        outcome=CallOutcome.RESOLVED_BY_AGENT,
        marks=(TimelineMark(NOW, MarkKind.TRANSITION, "received"),),
    )
    rendered = repr(outline)
    assert "Parcel desk" not in rendered
    assert "2025550199" not in rendered
    assert "gate" not in rendered


async def test_a_call_with_no_escalation_summary_or_marks_is_outlined_as_what_it_has(
    session: AsyncSession,
) -> None:
    await a_stored_call(session)

    outline = await SqlCallTimelineRepository(session).outline(CALL)

    assert outline is not None
    assert (outline.escalation, outline.outcome, outline.marks) == (None, None, ())


async def test_no_call_is_no_outline(session: AsyncSession) -> None:
    assert await SqlCallTimelineRepository(session).outline(CallId("never-stored")) is None


async def test_marks_go_with_their_call(session: AsyncSession) -> None:
    await a_stored_call(session)
    timeline = SqlCallTimelineRepository(session)
    await timeline.append(CALL, (TimelineMark(NOW, MarkKind.TRANSITION, "received"),))

    await SqlCallRepository(session, CIPHER, FixedClock(NOW)).delete(ME, CALL)

    assert await timeline.outline(CALL) is None
    remaining = await session.execute(text("SELECT count(*) FROM call_timeline_marks"))
    assert remaining.scalar_one() == 0


async def test_a_mark_for_a_call_never_stored_is_refused(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        await SqlCallTimelineRepository(session).append(
            CallId("never-stored"), (TimelineMark(NOW, MarkKind.TRANSITION, "received"),)
        )
