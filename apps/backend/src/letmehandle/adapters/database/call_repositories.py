"""Calls, transcripts, summaries and escalation contexts, implemented against PostgreSQL.

In a module of their own because all four hold a cipher, and nothing else in the database
adapter does: whatever a transcript, a summary or an escalation says, and who the caller was, is
sealed before it reaches a statement and opened after it leaves one, so none of it ever becomes a
bound parameter.

Every read filters by the owner, and every write against a call first proves the call is the
writer's. The composite foreign keys underneath enforce the same thing a second time.
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, delete, exists, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from letmehandle.domain.errors import AlreadyRecordedError, InvariantError, RecordNotFoundError
from letmehandle.domain.models.call import (
    CallHandling,
    CallSession,
    Participant,
    ParticipantRole,
    Speaker,
    TranscriptEntry,
)
from letmehandle.domain.models.call_state import TERMINAL, CallState
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome, CallSummary, ExtractedDetail
from letmehandle.domain.ports.repositories import (
    MAX_CALL_PAGE,
    MAX_PURGE_BATCH,
    CallCursor,
    CallFilter,
    CallPage,
    CallRepository,
    EscalationContextRepository,
    SummaryRepository,
    TranscriptRepository,
    TranscriptRetentionRepository,
    TranscriptStatus,
    check_page_size,
)
from letmehandle.domain.ports.security import SealedBytes

from .models import (
    CallParticipantRow,
    CallRow,
    CallSummaryRow,
    EscalationContextRow,
    TranscriptEntryRow,
)
from .repositories import _affected

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.security import TranscriptCipher


async def _owns_call(
    session: AsyncSession, user_id: UserId, call_id: CallId, *, lock: bool = False
) -> bool:
    """Whether the call is this user's; with `lock`, holding its row until the transaction ends."""
    query = select(CallRow.id).where(CallRow.id == call_id.value, CallRow.user_id == user_id.value)
    if lock:
        query = query.with_for_update()
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


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
            "handling": None if call.handling is None else call.handling.value,
            "escalated_at": call.escalated_at,
            "updated_at": self._clock.now(),
        }
        statement = insert(CallRow).values(
            id=call.id.value, user_id=call.user_id.value, started_at=call.started_at, **values
        )
        excluded = statement.excluded
        # One statement, and conditional on the owner. An identifier that already belongs to
        # somebody else's call updates nothing rather than taking the row over. Nor does a call
        # that has ended take a write that would move it anywhere: the same call announced again
        # after its ending must not turn a finished record back into a live one. The same ending
        # written again is let through, because a write that was stored but reported as failed is
        # tried again.
        result = await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[CallRow.id],
                set_=values,
                where=(CallRow.user_id == excluded.user_id)
                & (
                    CallRow.state.not_in([state.value for state in TERMINAL])
                    | ((CallRow.state == excluded.state) & (CallRow.ended_at == excluded.ended_at))
                ),
            )
        )
        if _affected(result) == 0:
            owner = await self._session.scalar(
                select(CallRow.user_id).where(CallRow.id == call.id.value)
            )
            if owner == call.user_id.value:
                raise AlreadyRecordedError("call", call.id.value)
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
        self,
        user_id: UserId,
        *,
        limit: int,
        after: CallCursor | None = None,
        matching: CallFilter | None = None,
    ) -> CallPage:
        check_page_size(limit, MAX_CALL_PAGE)
        query = select(CallRow).where(CallRow.user_id == user_id.value)
        if matching is not None:
            query = _matching(query, matching)
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

    async def unfinished(self, *, limit: int) -> tuple[CallSession, ...]:
        check_page_size(limit, MAX_CALL_PAGE)
        result = await self._session.execute(
            select(CallRow)
            .where(CallRow.ended_at.is_(None))
            .order_by(CallRow.started_at, CallRow.id)
            .limit(limit)
        )
        rows = list(result.scalars().all())
        participants = await self._participants([row.id for row in rows])
        return tuple(self._to_call(row, participants.get(row.id, ())) for row in rows)

    async def delete(self, user_id: UserId, call_id: CallId) -> None:
        # One statement. The participants, transcript lines and summary are removed by the
        # foreign keys' cascades within it, so there is no moment — and no failure part-way —
        # at which the call is gone and something said on it is not. A transcript being appended
        # holds the call's row, so this waits for those lines and takes them too.
        await self._session.execute(
            delete(CallRow).where(CallRow.id == call_id.value, CallRow.user_id == user_id.value)
        )
        # Not a cascade: the escalation service stores the context on its own, naming the call by
        # identifier and holding no key to its row. It repeats who called and what they wanted,
        # so it goes in the same transaction as the call it describes.
        await self._session.execute(
            delete(EscalationContextRow).where(
                EscalationContextRow.user_id == user_id.value,
                EscalationContextRow.call_id == call_id.value,
            )
        )
        await self._session.flush()

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
            handling=None if row.handling is None else CallHandling(row.handling),
            escalated_at=row.escalated_at,
        )


def _matching(query: Select[tuple[CallRow]], matching: CallFilter) -> Select[tuple[CallRow]]:
    """The history query, narrowed to the calls the filter allows."""
    if matching.started_from is not None:
        query = query.where(CallRow.started_at >= matching.started_from)
    if matching.started_before is not None:
        query = query.where(CallRow.started_at < matching.started_before)
    if matching.outcome is None and matching.human_joined is None:
        return query
    # An inner join: a call with no summary has no outcome, so it matches no filter on one. The
    # owner is joined on as well as the call, as the foreign key underneath already insists.
    query = query.join(
        CallSummaryRow,
        (CallSummaryRow.call_id == CallRow.id) & (CallSummaryRow.user_id == CallRow.user_id),
    )
    if matching.outcome is not None:
        query = query.where(CallSummaryRow.outcome == matching.outcome.value)
    if matching.human_joined is not None:
        joined = CallSummaryRow.human_joined_at
        query = query.where(joined.is_not(None) if matching.human_joined else joined.is_(None))
    return query


def _transcript_context(
    user_id: str, call_id: str, sequence: int, speaker: str, said_at: datetime
) -> tuple[str, ...]:
    """What a transcript entry's ciphertext is bound to.

    The speaker and the moment as well as the owner and the call: a row whose speaker column is
    changed — so that the caller's "yes" becomes the assistant's — no longer opens. And its place
    in the call: a row copied under another number no longer opens either, so a line said once
    cannot be made to appear twice.
    """
    return ("transcript", user_id, call_id, str(sequence), speaker, _moment(said_at))


def _moment(instant: datetime) -> str:
    """An instant as bound into a context: the same text for the same instant in any zone."""
    if instant.tzinfo is None:
        raise InvariantError(
            "a sealed record needs a timezone-aware time; a naive one means whatever the "
            "server is set to, and would not open again after a move between regions"
        )
    return instant.astimezone(UTC).isoformat(timespec="microseconds")


class SqlTranscriptRepository(TranscriptRepository):
    """Transcripts, sealed line by line and numbered within their call.

    The numbers are what make a missing line visible. Every entry is bound to its number, and a
    read refuses a transcript whose numbers skip or repeat, so a row deleted from the middle or
    copied within the call is detected rather than silently changing what was said.

    Two deletions are not detectable, by design. The oldest lines going is exactly what the
    purge does, so a transcript may start at any number. The newest line going leaves nothing
    after it to disagree. Both need write access to the database, which is already a breach
    this cannot repair; what it does guarantee is that what is read was said, in that order,
    with nothing taken out of the middle.
    """

    def __init__(self, session: AsyncSession, cipher: TranscriptCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def append(
        self, user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
    ) -> None:
        # The call's row is locked, so two appends to one call number their lines one after the
        # other instead of both reading the same last number and one failing on the constraint.
        if not await _owns_call(self._session, user_id, call_id, lock=True):
            raise RecordNotFoundError("call", call_id.value)
        if not entries:
            return
        last = await self._session.execute(
            select(func.max(TranscriptEntryRow.sequence)).where(
                TranscriptEntryRow.call_id == call_id.value
            )
        )
        highest = last.scalar_one()
        first = 0 if highest is None else highest + 1
        rows = []
        for sequence, entry in enumerate(entries, start=first):
            sealed = self._cipher.seal(
                entry.text.encode(),
                _transcript_context(
                    user_id.value, call_id.value, sequence, entry.speaker.value, entry.at_instant
                ),
            )
            rows.append(
                {
                    "user_id": user_id.value,
                    "call_id": call_id.value,
                    "sequence": sequence,
                    "speaker": entry.speaker.value,
                    "said_at": entry.at_instant,
                    "key_id": sealed.key_id,
                    "ciphertext": sealed.ciphertext,
                }
            )
        await self._session.execute(insert(TranscriptEntryRow).values(rows))
        await self._session.execute(
            update(CallRow).where(CallRow.id == call_id.value).values(transcript_recorded=True)
        )
        await self._session.flush()

    async def for_call(self, user_id: UserId, call_id: CallId) -> tuple[TranscriptEntry, ...]:
        result = await self._session.execute(
            select(TranscriptEntryRow)
            .where(
                TranscriptEntryRow.user_id == user_id.value,
                TranscriptEntryRow.call_id == call_id.value,
            )
            .order_by(TranscriptEntryRow.said_at, TranscriptEntryRow.sequence)
        )
        rows = list(result.scalars().all())
        numbers = sorted(row.sequence for row in rows)
        if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise InvariantError(
                "a stored transcript is missing a line from its middle or holds one twice, so "
                "what it says is not what was said"
            )
        return tuple(
            TranscriptEntry(
                speaker=Speaker(row.speaker),
                text=self._cipher.open(
                    SealedBytes(row.key_id, row.ciphertext),
                    _transcript_context(
                        row.user_id, row.call_id, row.sequence, row.speaker, row.said_at
                    ),
                ).decode(),
                at_instant=row.said_at,
            )
            for row in rows
        )

    async def status(self, user_id: UserId, call_id: CallId) -> TranscriptStatus:
        remaining = exists().where(
            TranscriptEntryRow.call_id == call_id.value,
            TranscriptEntryRow.user_id == user_id.value,
        )
        result = await self._session.execute(
            select(CallRow.transcript_recorded, remaining).where(
                CallRow.id == call_id.value, CallRow.user_id == user_id.value
            )
        )
        row = result.one_or_none()
        if row is None or not row[0]:
            return TranscriptStatus.NOT_RECORDED
        # Recorded and nothing left can only be the purge: nothing else deletes a line and keeps
        # the call.
        return TranscriptStatus.RETAINED if row[1] else TranscriptStatus.PURGED


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
        #
        # Only a call's leading lines go. Lines are numbered in the order they were written, which
        # is not always the order they were said, and a read refuses a transcript with a line
        # missing from its middle; so an expired line waits while a line numbered before it is
        # kept, and goes when that one does. Oldest numbers first, so that a batch committed
        # part-way through a call leaves it readable. Two purges racing can still leave a gap
        # between their batches until the slower one commits.
        earlier = aliased(TranscriptEntryRow)
        kept_before_it = exists().where(
            earlier.call_id == TranscriptEntryRow.call_id,
            earlier.sequence < TranscriptEntryRow.sequence,
            earlier.said_at > at_or_before,
        )
        chosen = (
            select(TranscriptEntryRow.id)
            .where(
                TranscriptEntryRow.user_id == user_id.value,
                TranscriptEntryRow.said_at <= at_or_before,
                ~kept_before_it,
            )
            .order_by(TranscriptEntryRow.call_id, TranscriptEntryRow.sequence)
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
        return None if row is None else self._to_summary(row)

    async def for_calls(
        self, user_id: UserId, call_ids: Sequence[CallId]
    ) -> Mapping[CallId, CallSummary]:
        if not call_ids:
            return {}
        result = await self._session.execute(
            select(CallSummaryRow).where(
                CallSummaryRow.user_id == user_id.value,
                CallSummaryRow.call_id.in_([call_id.value for call_id in call_ids]),
            )
        )
        return {CallId(row.call_id): self._to_summary(row) for row in result.scalars().all()}

    def _to_summary(self, row: CallSummaryRow) -> CallSummary:
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


def _escalation_context(
    user_id: str, call_id: str, *, reason: str, raised_at: datetime
) -> tuple[str, ...]:
    """What an escalation's sealed words are bound to: its owner, its call, and why and when.

    The reason and the moment are the columns that never change once the escalation is claimed.
    Bound here, words moved onto another escalation, or an escalation changed to say it was about
    something else, no longer open.
    """
    return ("escalation", user_id, call_id, reason, _moment(raised_at))


class SqlEscalationContextRepository(EscalationContextRepository):
    """What each user was told about each escalation, with the words sealed."""

    def __init__(self, session: AsyncSession, cipher: TranscriptCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def claim(self, user_id: UserId, context: EscalationContext) -> bool:
        words = {
            "caller_label": context.caller_label,
            "established": context.established,
            "needed": context.needed,
        }
        sealed = (
            None
            if all(value is None for value in words.values())
            else self._cipher.seal(
                json.dumps(words).encode(),
                _escalation_context(
                    user_id.value,
                    context.call_id.value,
                    reason=context.reason.value,
                    raised_at=context.raised_at,
                ),
            )
        )
        # One statement, so two dispatches racing for the same call cannot both win: the database
        # decides which insert happened, and the other sees nothing returned.
        statement = (
            insert(EscalationContextRow)
            .values(
                user_id=user_id.value,
                call_id=context.call_id.value,
                reason=context.reason.value,
                key_id=None if sealed is None else sealed.key_id,
                ciphertext=None if sealed is None else sealed.ciphertext,
                status=context.status.value,
                delivery=context.delivery.value,
                raised_at=context.raised_at,
                ended_at=context.ended_at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "call_id"])
            .returning(EscalationContextRow.call_id)
        )
        claimed = (await self._session.execute(statement)).scalar_one_or_none()
        return claimed is not None

    async def get(self, user_id: UserId, call_id: CallId) -> EscalationContext | None:
        row = await self._session.get(EscalationContextRow, (user_id.value, call_id.value))
        return None if row is None else self._to_context(row)

    async def record_delivery(
        self, user_id: UserId, call_id: CallId, delivery: NotificationDelivery
    ) -> None:
        await self._session.execute(
            update(EscalationContextRow)
            .where(
                EscalationContextRow.user_id == user_id.value,
                EscalationContextRow.call_id == call_id.value,
            )
            .values(delivery=delivery.value)
        )

    async def mark_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        row = await self._session.get(
            EscalationContextRow, (user_id.value, call_id.value), with_for_update=True
        )
        if row is None:
            return False
        # Through the domain, so an end before the escalation is refused rather than stored.
        ended = self._to_context(row).ended(at_instant)
        row.status = ended.status.value
        row.ended_at = ended.ended_at
        await self._session.flush()
        return True

    def _to_context(self, row: EscalationContextRow) -> EscalationContext:
        words: dict[str, str | None] = {}
        if row.key_id is not None and row.ciphertext is not None:
            words = json.loads(
                self._cipher.open(
                    SealedBytes(row.key_id, row.ciphertext),
                    _escalation_context(
                        row.user_id, row.call_id, reason=row.reason, raised_at=row.raised_at
                    ),
                )
            )
        return EscalationContext(
            call_id=CallId(row.call_id),
            reason=EscalationReason(row.reason),
            raised_at=row.raised_at,
            caller_label=words.get("caller_label"),
            established=words.get("established"),
            needed=words.get("needed"),
            status=EscalationStatus(row.status),
            ended_at=row.ended_at,
            delivery=NotificationDelivery(row.delivery),
        )
