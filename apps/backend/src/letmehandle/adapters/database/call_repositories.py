"""Calls, transcripts and summaries, implemented against PostgreSQL.

In a module of their own because all three hold a cipher, and nothing else in the database
adapter does: whatever a transcript or a summary says, and who the caller was, is sealed before
it reaches a statement and opened after it leaves one, so none of it ever becomes a bound
parameter.

Every read filters by the owner, and every write against a call first proves the call is the
writer's. The composite foreign keys underneath enforce the same thing a second time.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from letmehandle.domain.errors import AlreadyRecordedError, InvariantError, RecordNotFoundError
from letmehandle.domain.models.call import (
    CallSession,
    Participant,
    ParticipantRole,
    Speaker,
    TranscriptEntry,
)
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome, CallSummary, ExtractedDetail
from letmehandle.domain.ports.repositories import (
    MAX_CALL_PAGE,
    MAX_PURGE_BATCH,
    CallCursor,
    CallPage,
    CallRepository,
    SummaryRepository,
    TranscriptRepository,
    TranscriptRetentionRepository,
    check_page_size,
)
from letmehandle.domain.ports.security import SealedBytes

from .models import CallParticipantRow, CallRow, CallSummaryRow, TranscriptEntryRow
from .repositories import _affected

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.security import TranscriptCipher


async def _owns_call(session: AsyncSession, user_id: UserId, call_id: CallId) -> bool:
    result = await session.execute(
        select(exists().where(CallRow.id == call_id.value, CallRow.user_id == user_id.value))
    )
    return bool(result.scalar_one())


def _identity_to_document(caller: Caller) -> dict[str, str | None]:
    """Who the caller is: the half of a `Caller` that identifies a person."""
    return {
        "number": None if caller.number is None else caller.number.value,
        "display_name": caller.display_name,
    }


def _caller_from(identity: dict[str, Any], category: str) -> Caller:
    return Caller(
        number=None if identity["number"] is None else PhoneNumber(identity["number"]),
        display_name=identity["display_name"],
        category=CallerCategory(category),
    )


def _caller_context(user_id: str, call_id: str) -> tuple[str, ...]:
    """What a call's sealed caller is bound to, so it opens on no other call or user's row."""
    return ("caller", user_id, call_id)


class SqlCallRepository(CallRepository):
    """Calls, with who called sealed.

    The number and the name are sealed together, under the same key and bound to the owner and
    the call; the category stays a column, being a classification rather than an identity. A
    withheld caller is sealed too, so a dump cannot tell which calls had a number.
    """

    def __init__(self, session: AsyncSession, cipher: TranscriptCipher, clock: Clock) -> None:
        self._session = session
        self._cipher = cipher
        self._clock = clock

    async def save(self, call: CallSession) -> None:
        sealed = self._cipher.seal(
            json.dumps(_identity_to_document(call.caller)).encode(),
            _caller_context(call.user_id.value, call.id.value),
        )
        values = {
            "state": call.state.value,
            "key_id": sealed.key_id,
            "caller_ciphertext": sealed.ciphertext,
            "caller_category": call.caller.category.value,
            "ended_at": call.ended_at,
            "updated_at": self._clock.now(),
        }
        statement = insert(CallRow).values(
            id=call.id.value, user_id=call.user_id.value, started_at=call.started_at, **values
        )
        # One statement, and conditional on the owner. An identifier that already belongs to
        # somebody else's call updates nothing rather than taking the row over.
        result = await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[CallRow.id],
                set_=values,
                where=CallRow.user_id == statement.excluded.user_id,
            )
        )
        if _affected(result) == 0:
            raise RecordNotFoundError("call", call.id.value)

        # Participants are written whole with the call: the list is short, and replacing it is
        # the one write that cannot leave a departure recorded against the wrong entry.
        await self._session.execute(
            delete(CallParticipantRow).where(CallParticipantRow.call_id == call.id.value)
        )
        if call.participants:
            await self._session.execute(
                insert(CallParticipantRow).values(
                    [
                        {
                            "call_id": call.id.value,
                            "position": position,
                            "role": participant.role.value,
                            "joined_at": participant.joined_at,
                            "left_at": participant.left_at,
                        }
                        for position, participant in enumerate(call.participants)
                    ]
                )
            )
        await self._session.flush()

    async def get(self, user_id: UserId, call_id: CallId) -> CallSession | None:
        result = await self._session.execute(
            select(CallRow).where(CallRow.id == call_id.value, CallRow.user_id == user_id.value)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        participants = await self._participants([row.id])
        return self._to_call(row, participants.get(row.id, ()))

    async def list_for_user(
        self, user_id: UserId, *, limit: int, after: CallCursor | None = None
    ) -> CallPage:
        check_page_size(limit, MAX_CALL_PAGE)
        query = select(CallRow).where(CallRow.user_id == user_id.value)
        if after is not None:
            query = query.where(
                tuple_(CallRow.started_at, CallRow.id) < (after.started_at, after.call_id.value)
            )
        # One more than asked for, so whether there is a next page is known without a count.
        result = await self._session.execute(
            query.order_by(CallRow.started_at.desc(), CallRow.id.desc()).limit(limit + 1)
        )
        rows = list(result.scalars().all())
        page, more = rows[:limit], len(rows) > limit
        participants = await self._participants([row.id for row in page])
        calls = tuple(self._to_call(row, participants.get(row.id, ())) for row in page)
        last = page[-1] if more else None
        return CallPage(
            calls=calls,
            next_cursor=None if last is None else CallCursor(last.started_at, CallId(last.id)),
        )

    async def _participants(self, call_ids: list[str]) -> dict[str, tuple[Participant, ...]]:
        if not call_ids:
            return {}
        result = await self._session.execute(
            select(CallParticipantRow)
            .where(CallParticipantRow.call_id.in_(call_ids))
            .order_by(CallParticipantRow.call_id, CallParticipantRow.position)
        )
        grouped: dict[str, list[Participant]] = {}
        for row in result.scalars().all():
            grouped.setdefault(row.call_id, []).append(
                Participant(ParticipantRole(row.role), row.joined_at, row.left_at)
            )
        return {call_id: tuple(each) for call_id, each in grouped.items()}

    def _to_call(self, row: CallRow, participants: tuple[Participant, ...]) -> CallSession:
        identity = json.loads(
            self._cipher.open(
                SealedBytes(row.key_id, row.caller_ciphertext),
                _caller_context(row.user_id, row.id),
            )
        )
        return CallSession.restore(
            id=CallId(row.id),
            user_id=UserId(row.user_id),
            caller=_caller_from(identity, row.caller_category),
            started_at=row.started_at,
            state=CallState(row.state),
            participants=participants,
            ended_at=row.ended_at,
        )


def _transcript_context(
    user_id: str, call_id: str, speaker: str, said_at: datetime
) -> tuple[str, ...]:
    """What a transcript entry's ciphertext is bound to.

    The speaker and the moment as well as the owner and the call: a row whose speaker column is
    changed — so that the caller's "yes" becomes the assistant's — no longer opens.
    """
    return ("transcript", user_id, call_id, speaker, _moment(said_at))


def _moment(instant: datetime) -> str:
    """An instant as bound into a context: the same text for the same instant in any zone."""
    if instant.tzinfo is None:
        raise InvariantError(
            "a sealed record needs a timezone-aware time; a naive one means whatever the "
            "server is set to, and would not open again after a move between regions"
        )
    return instant.astimezone(UTC).isoformat(timespec="microseconds")


class SqlTranscriptRepository(TranscriptRepository):
    def __init__(self, session: AsyncSession, cipher: TranscriptCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def append(
        self, user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
    ) -> None:
        if not await _owns_call(self._session, user_id, call_id):
            raise RecordNotFoundError("call", call_id.value)
        if not entries:
            return
        rows = []
        for entry in entries:
            sealed = self._cipher.seal(
                entry.text.encode(),
                _transcript_context(
                    user_id.value, call_id.value, entry.speaker.value, entry.at_instant
                ),
            )
            rows.append(
                {
                    "user_id": user_id.value,
                    "call_id": call_id.value,
                    "speaker": entry.speaker.value,
                    "said_at": entry.at_instant,
                    "key_id": sealed.key_id,
                    "ciphertext": sealed.ciphertext,
                }
            )
        await self._session.execute(insert(TranscriptEntryRow).values(rows))
        await self._session.flush()

    async def for_call(self, user_id: UserId, call_id: CallId) -> tuple[TranscriptEntry, ...]:
        result = await self._session.execute(
            select(TranscriptEntryRow)
            .where(
                TranscriptEntryRow.user_id == user_id.value,
                TranscriptEntryRow.call_id == call_id.value,
            )
            .order_by(TranscriptEntryRow.said_at, TranscriptEntryRow.id)
        )
        return tuple(
            TranscriptEntry(
                speaker=Speaker(row.speaker),
                text=self._cipher.open(
                    SealedBytes(row.key_id, row.ciphertext),
                    _transcript_context(row.user_id, row.call_id, row.speaker, row.said_at),
                ).decode(),
                at_instant=row.said_at,
            )
            for row in result.scalars().all()
        )


class SqlTranscriptRetentionRepository(TranscriptRetentionRepository):
    """The purge's view of transcripts: who has old entries, and deleting them. No cipher."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def users_with_entries_at_or_before(
        self, cutoff: datetime, *, after: UserId | None, limit: int
    ) -> list[UserId]:
        check_page_size(limit, MAX_PURGE_BATCH)
        query = select(TranscriptEntryRow.user_id).where(TranscriptEntryRow.said_at <= cutoff)
        if after is not None:
            query = query.where(TranscriptEntryRow.user_id > after.value)
        result = await self._session.execute(
            query.distinct().order_by(TranscriptEntryRow.user_id).limit(limit)
        )
        return [UserId(value) for value in result.scalars().all()]

    async def delete_expired(self, user_id: UserId, *, at_or_before: datetime, limit: int) -> int:
        check_page_size(limit, MAX_PURGE_BATCH)
        # The rows are chosen and locked first, skipping any another purge has already locked.
        # Two purges running together therefore never wait on each other, never deadlock, and
        # never both delete — or both count — the same row: a row is either locked by one of
        # them or already gone from the other's snapshot.
        #
        # A materialised CTE rather than `id IN (subquery)`, and not for style. PostgreSQL may
        # evaluate a locking subquery more than once inside the delete's plan; each evaluation
        # skips what the last one locked and returns fresh rows, so the LIMIT stops bounding
        # anything. Measured here: a batch of ten deleted thirty. Materialising runs it once.
        chosen = (
            select(TranscriptEntryRow.id)
            .where(
                TranscriptEntryRow.user_id == user_id.value,
                TranscriptEntryRow.said_at <= at_or_before,
            )
            .order_by(TranscriptEntryRow.said_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .cte("chosen")
            .prefix_with("MATERIALIZED")
        )
        result = await self._session.execute(
            delete(TranscriptEntryRow).where(
                TranscriptEntryRow.user_id == user_id.value,
                TranscriptEntryRow.id.in_(select(chosen.c.id)),
            )
        )
        return _affected(result)


def _summary_context(
    user_id: str,
    call_id: str,
    *,
    outcome: str,
    intent: str,
    importance: int,
    escalation_reason: str | None,
    started_at: datetime,
    ended_at: datetime,
    human_joined_at: datetime | None,
) -> tuple[str, ...]:
    """What a summary's ciphertext is bound to: its owner, its call, and every readable column.

    The columns are what the summary says happened, kept readable only so history can filter on
    them. Bound here, a row changed to make an urgent call routine, to erase an escalation or to
    move when somebody joined no longer opens. An absent value is the empty string, which no
    present one can be.
    """
    return (
        "summary",
        user_id,
        call_id,
        outcome,
        intent,
        str(importance),
        escalation_reason or "",
        _moment(started_at),
        _moment(ended_at),
        "" if human_joined_at is None else _moment(human_joined_at),
    )


def _summary_to_document(summary: CallSummary) -> dict[str, Any]:
    caller = summary.caller
    return {
        "caller": {**_identity_to_document(caller), "category": caller.category.value},
        "headline": summary.headline,
        "details": [
            {"label": each.label, "value": each.value, "evidence": each.evidence}
            for each in summary.details
        ],
    }


class SqlSummaryRepository(SummaryRepository):
    def __init__(self, session: AsyncSession, cipher: TranscriptCipher, clock: Clock) -> None:
        self._session = session
        self._cipher = cipher
        self._clock = clock

    async def add(self, user_id: UserId, summary: CallSummary) -> None:
        call_id = summary.call_id
        if not await _owns_call(self._session, user_id, call_id):
            raise RecordNotFoundError("call", call_id.value)
        columns: dict[str, Any] = {
            "outcome": summary.outcome.value,
            "intent": summary.intent.value,
            "importance": int(summary.importance),
            "escalation_reason": (
                None if summary.escalation_reason is None else summary.escalation_reason.value
            ),
            "started_at": summary.started_at,
            "ended_at": summary.ended_at,
            "human_joined_at": summary.human_joined_at,
        }
        sealed = self._cipher.seal(
            json.dumps(_summary_to_document(summary)).encode(),
            _summary_context(user_id.value, call_id.value, **columns),
        )
        result = await self._session.execute(
            insert(CallSummaryRow)
            .values(
                call_id=call_id.value,
                user_id=user_id.value,
                **columns,
                key_id=sealed.key_id,
                ciphertext=sealed.ciphertext,
                created_at=self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[CallSummaryRow.call_id])
        )
        if _affected(result) == 0:
            raise AlreadyRecordedError("summary", call_id.value)
        await self._session.flush()

    async def get(self, user_id: UserId, call_id: CallId) -> CallSummary | None:
        result = await self._session.execute(
            select(CallSummaryRow).where(
                CallSummaryRow.call_id == call_id.value,
                CallSummaryRow.user_id == user_id.value,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        document = json.loads(
            self._cipher.open(
                SealedBytes(row.key_id, row.ciphertext),
                _summary_context(
                    row.user_id,
                    row.call_id,
                    outcome=row.outcome,
                    intent=row.intent,
                    importance=row.importance,
                    escalation_reason=row.escalation_reason,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    human_joined_at=row.human_joined_at,
                ),
            )
        )
        caller = document["caller"]
        return CallSummary(
            call_id=CallId(row.call_id),
            caller=_caller_from(caller, caller["category"]),
            intent=CallIntent(row.intent),
            importance=CallImportance(row.importance),
            outcome=CallOutcome(row.outcome),
            headline=document["headline"],
            started_at=row.started_at,
            ended_at=row.ended_at,
            human_joined_at=row.human_joined_at,
            escalation_reason=(
                None if row.escalation_reason is None else EscalationReason(row.escalation_reason)
            ),
            details=tuple(
                ExtractedDetail(each["label"], each["value"], each["evidence"])
                for each in document["details"]
            ),
        )
