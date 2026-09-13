"""Calls, transcripts and summaries through a real database.

Three promises are tested here, and each has the failure it exists to prevent: a call that
comes back different from how it went in, one user reaching another's record, and anything
somebody said sitting in the database where a dump could read it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
    SqlTranscriptRetentionRepository,
)
from letmehandle.adapters.database.engine import create_engine
from letmehandle.adapters.database.repositories import SqlUserRepository
from letmehandle.adapters.security.transcript_cipher import AesGcmTranscriptCipher
from letmehandle.domain.errors import (
    AlreadyRecordedError,
    DecryptionError,
    InvariantError,
    RecordNotFoundError,
    UnknownKeyError,
)
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
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import CallOutcome, CallSummary, ExtractedDetail
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE, MAX_PURGE_BATCH, CallCursor
from tests.contracts.fakes import FixedClock
from tests.support.config import make_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ME = UserId("user-1")
THEM = UserId("user-2")
CALLER_NUMBER = PhoneNumber.parse("+12025550199")

KEY_A = ("key-a", bytes(range(32)))
KEY_B = ("key-b", bytes(range(32, 64)))

# Distinctive enough that finding it anywhere in a table can only mean it was stored in clear.
SECRET_WORDS = "the gate code is violet-kestrel-5521"
SECRET_EVIDENCE = "reference number ZX-kestrel-9043"


def later(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


async def users(session: AsyncSession) -> None:
    repository = SqlUserRepository(session, FixedClock(NOW))
    await repository.add(User(id=ME, phone_number=PhoneNumber.parse("+12025550143")))
    await repository.add(User(id=THEM, phone_number=PhoneNumber.parse("+12025550144")))


def a_call(
    call_id: str = "call-1",
    user_id: UserId = ME,
    *,
    started_at: datetime = NOW,
    state: CallState = CallState.AGENT_HANDLING,
) -> CallSession:
    return CallSession.restore(
        id=CallId(call_id),
        user_id=user_id,
        caller=Caller(
            number=CALLER_NUMBER, display_name="Parcel desk", category=CallerCategory.DELIVERY
        ),
        started_at=started_at,
        state=state,
        participants=(),
        ended_at=started_at + timedelta(minutes=2) if state in TERMINAL else None,
    )


@pytest.fixture
def calls(session: AsyncSession) -> SqlCallRepository:
    return SqlCallRepository(session, AesGcmTranscriptCipher([KEY_A]), FixedClock(NOW))


@pytest.fixture
def transcripts(session: AsyncSession) -> SqlTranscriptRepository:
    return SqlTranscriptRepository(session, AesGcmTranscriptCipher([KEY_A]))


@pytest.fixture
def summaries(session: AsyncSession) -> SqlSummaryRepository:
    return SqlSummaryRepository(session, AesGcmTranscriptCipher([KEY_A]), FixedClock(NOW))


class TestCalls:
    @pytest.mark.parametrize("state", list(CallState))
    async def test_a_call_in_every_state_round_trips(
        self, session: AsyncSession, calls: SqlCallRepository, state: CallState
    ) -> None:
        await users(session)
        call = a_call(state=state)
        await calls.save(call)
        assert await calls.get(ME, call.id) == call

    async def test_a_whole_life_with_its_participant_history_round_trips(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        call = CallSession(id=CallId("call-1"), user_id=ME, caller=Caller(), started_at=NOW)
        call.add_participant(ParticipantRole.CALLER, NOW)
        call.move_to(CallState.ROUTING)
        await calls.save(call)

        call.move_to(CallState.AGENT_HANDLING)
        call.add_participant(ParticipantRole.AGENT, later(1))
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        call.move_to(CallState.HUMAN_RINGING)
        call.move_to(CallState.HUMAN_JOINED)
        call.add_participant(ParticipantRole.HUMAN, later(20))
        call.remove_participant(ParticipantRole.AGENT, later(21))
        call.remove_participant(ParticipantRole.HUMAN, later(40))
        call.add_participant(ParticipantRole.HUMAN, later(50))
        call.move_to(CallState.COMPLETED, at_instant=later(90))
        await calls.save(call)

        stored = await calls.get(ME, call.id)
        assert stored == call
        assert stored is not None
        assert [p.role for p in stored.participants] == [
            ParticipantRole.CALLER,
            ParticipantRole.AGENT,
            ParticipantRole.HUMAN,
            ParticipantRole.HUMAN,
        ]
        assert stored.participants[1] == Participant(ParticipantRole.AGENT, later(1), later(21))
        assert stored.caller == Caller()
        assert stored.duration_seconds() == 90
        assert stored.handling is CallHandling.ASSISTANT
        assert stored.escalated_at == later(10)

    async def test_saving_again_replaces_the_participants(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        call = CallSession(id=CallId("call-1"), user_id=ME, caller=Caller(), started_at=NOW)
        call.add_participant(ParticipantRole.CALLER, NOW)
        await calls.save(call)
        call.remove_participant(ParticipantRole.CALLER, later(5))
        await calls.save(call)

        stored = await calls.get(ME, call.id)
        assert stored is not None
        assert stored.participants == (Participant(ParticipantRole.CALLER, NOW, later(5)),)

    async def test_who_called_is_not_stored_in_clear(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        # The summary seals who the caller was taken to be; a plain column beside it would
        # leave a dump saying who called whom all the same.
        await users(session)
        await calls.save(a_call())
        await assert_nowhere_in(session, "calls", 1, CALLER_NUMBER.value)
        await assert_nowhere_in(session, "calls", 1, "Parcel desk")

    async def test_a_withheld_caller_round_trips_sealed(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        call = CallSession(id=CallId("call-1"), user_id=ME, caller=Caller(), started_at=NOW)
        await calls.save(call)
        assert await calls.get(ME, call.id) == call

    async def test_a_caller_moved_onto_another_call_no_longer_opens(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call("call-1"))
        await calls.save(
            CallSession(id=CallId("call-2"), user_id=ME, caller=Caller(), started_at=later(5))
        )
        await session.execute(
            text(
                "UPDATE calls SET caller_ciphertext = "
                "(SELECT caller_ciphertext FROM calls WHERE id = 'call-1') WHERE id = 'call-2'"
            )
        )

        with pytest.raises(DecryptionError):
            await calls.get(ME, CallId("call-2"))
        with pytest.raises(DecryptionError):
            await calls.list_for_user(ME, limit=10)

    async def test_one_user_cannot_read_another_s_call(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        assert await calls.get(THEM, CallId("call-1")) is None

    async def test_one_user_cannot_overwrite_another_s_call(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.AGENT_HANDLING))

        with pytest.raises(RecordNotFoundError):
            await calls.save(a_call(user_id=THEM, state=CallState.FAILED))

        mine = await calls.get(ME, CallId("call-1"))
        assert mine is not None
        assert mine.state is CallState.AGENT_HANDLING
        assert await calls.get(THEM, CallId("call-1")) is None

    async def test_a_call_whose_end_was_reported_before_its_start_saves_reads_and_lists(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        call = CallSession(id=CallId("call-1"), user_id=ME, caller=Caller(), started_at=NOW)
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.FAILED, at_instant=NOW - timedelta(milliseconds=5))
        await calls.save(call)

        assert await calls.get(ME, call.id) == call
        assert (await calls.list_for_user(ME, limit=10)).calls == (call,)


class TestDatabaseErrors:
    async def test_a_failed_statement_s_error_carries_none_of_its_values(
        self, session: AsyncSession, database_url: str, schema: str
    ) -> None:
        # A database error's text is what reaches a log line or an error tracker. With its
        # parameters rendered, a call that failed to save would write who called into it.
        engine = create_engine(make_settings(database_url=database_url))
        try:
            async with engine.connect() as connection:
                await connection.execute(text(f'SET search_path TO "{schema}"'))
                with pytest.raises(IntegrityError) as raised:
                    await connection.execute(
                        text(
                            "INSERT INTO user_preferences (user_id, version, document, updated_at) "
                            "VALUES (:user_id, 3, CAST(:document AS jsonb), :now)"
                        ),
                        {
                            "user_id": "nobody",
                            "document": json.dumps(
                                {
                                    "important_contacts": [
                                        {
                                            "number": CALLER_NUMBER.value,
                                            "label": "Wrenfield Parcel Desk",
                                        }
                                    ]
                                }
                            ),
                            "now": NOW,
                        },
                    )
        finally:
            await engine.dispose()

        printed = str(raised.value) + repr(raised.value)
        assert "fk" in printed.lower() or "foreign key" in printed
        assert CALLER_NUMBER.value not in printed
        assert "Wrenfield" not in printed


class TestHistory:
    async def test_calls_are_listed_newest_first_and_only_the_user_s_own(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call("old", started_at=later(0)))
        await calls.save(a_call("new", started_at=later(60)))
        await calls.save(a_call("theirs", THEM, started_at=later(30)))

        page = await calls.list_for_user(ME, limit=10)

        assert [call.id.value for call in page.calls] == ["new", "old"]
        assert page.next_cursor is None
        assert [c.id.value for c in (await calls.list_for_user(THEM, limit=10)).calls] == ["theirs"]

    async def test_pages_walk_the_whole_history_once_even_through_ties(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        # Five calls, three of them starting in the same instant: a cursor on the time alone
        # would skip or repeat one of those.
        for name, offset in [("a", 0), ("b", 10), ("c", 10), ("d", 10), ("e", 20)]:
            await calls.save(a_call(name, started_at=later(offset)))
        await calls.save(a_call("theirs", THEM, started_at=later(15)))

        seen: list[str] = []
        cursor: CallCursor | None = None
        pages = 0
        while True:
            page = await calls.list_for_user(ME, limit=2, after=cursor)
            seen.extend(call.id.value for call in page.calls)
            pages += 1
            if page.next_cursor is None:
                break
            cursor = page.next_cursor

        assert seen == ["e", "d", "c", "b", "a"]
        assert pages == 3

    async def test_a_page_that_exactly_fills_has_no_next(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call("a"))
        page = await calls.list_for_user(ME, limit=1)
        assert page.next_cursor is None

    async def test_nobody_s_history_is_empty(self, calls: SqlCallRepository) -> None:
        page = await calls.list_for_user(ME, limit=MAX_CALL_PAGE)
        assert page.calls == ()
        assert page.next_cursor is None

    @pytest.mark.parametrize("limit", [0, MAX_CALL_PAGE + 1])
    async def test_a_page_outside_the_bounds_is_refused(
        self, calls: SqlCallRepository, limit: int
    ) -> None:
        with pytest.raises(InvariantError):
            await calls.list_for_user(ME, limit=limit)


class TestUnfinished:
    async def test_every_user_s_unfinished_calls_are_found_oldest_first(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call("mine-late", started_at=later(20), state=CallState.HUMAN_RINGING))
        await calls.save(a_call("theirs-early", THEM, state=CallState.ROUTING))
        await calls.save(a_call("ended", started_at=later(5), state=CallState.COMPLETED))
        await calls.save(a_call("mine-middle", started_at=later(10), state=CallState.PASSTHROUGH))

        found = await calls.unfinished(limit=MAX_CALL_PAGE)

        assert [(call.id.value, call.user_id) for call in found] == [
            ("theirs-early", THEM),
            ("mine-middle", ME),
            ("mine-late", ME),
        ]
        # Whole calls, caller opened: what recovery records is what was stored.
        assert found[2] == a_call("mine-late", started_at=later(20), state=CallState.HUMAN_RINGING)

    async def test_at_most_a_page_is_found(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        for number in range(3):
            await calls.save(a_call(f"call-{number}", started_at=later(number)))
        found = await calls.unfinished(limit=2)
        assert [call.id.value for call in found] == ["call-0", "call-1"]

    @pytest.mark.parametrize("limit", [0, MAX_CALL_PAGE + 1])
    async def test_a_page_outside_the_bounds_is_refused(
        self, calls: SqlCallRepository, limit: int
    ) -> None:
        with pytest.raises(InvariantError):
            await calls.unfinished(limit=limit)


class TestTranscripts:
    async def test_entries_come_back_in_the_order_they_were_said(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        entries = [
            TranscriptEntry(Speaker.CALLER, SECRET_WORDS, later(1)),
            TranscriptEntry(Speaker.AGENT, "Thank you, I will pass that on.", later(2)),
            # The same instant as the next: insertion order decides.
            TranscriptEntry(Speaker.HUMAN, "Hello, it is me.", later(3)),
        ]
        await transcripts.append(ME, CallId("call-1"), entries[:2])
        await transcripts.append(ME, CallId("call-1"), entries[2:])
        await transcripts.append(ME, CallId("call-1"), [])

        assert await transcripts.for_call(ME, CallId("call-1")) == tuple(entries)

    async def test_a_time_in_another_zone_still_opens(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        elsewhere = later(1).astimezone(timezone(timedelta(hours=5, minutes=30)))
        await transcripts.append(
            ME, CallId("call-1"), [TranscriptEntry(Speaker.CALLER, "hello", elsewhere)]
        )
        (entry,) = await transcripts.for_call(ME, CallId("call-1"))
        assert entry.at_instant == elsewhere

    async def test_a_naive_time_is_refused(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        naive = TranscriptEntry(Speaker.CALLER, "hello", datetime(2026, 6, 1, 12, 0))
        with pytest.raises(InvariantError, match="timezone"):
            await transcripts.append(ME, CallId("call-1"), [naive])

    async def test_one_user_cannot_write_to_or_read_another_s_transcript(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME, CallId("call-1"), [TranscriptEntry(Speaker.CALLER, SECRET_WORDS, later(1))]
        )

        with pytest.raises(RecordNotFoundError):
            await transcripts.append(
                THEM, CallId("call-1"), [TranscriptEntry(Speaker.CALLER, "mine now", later(2))]
            )
        with pytest.raises(RecordNotFoundError):
            await transcripts.append(THEM, CallId("no-such-call"), [])

        assert await transcripts.for_call(THEM, CallId("call-1")) == ()
        assert len(await transcripts.for_call(ME, CallId("call-1"))) == 1

    async def test_the_database_refuses_an_entry_on_another_user_s_call(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        # The second line of defence: even a statement that skipped the repository's check.
        await users(session)
        await calls.save(a_call())
        with pytest.raises(Exception, match="fk_call_transcript_entries_call_user"):
            await session.execute(
                text(
                    "INSERT INTO call_transcript_entries "
                    "(user_id, call_id, sequence, speaker, said_at, key_id, ciphertext) "
                    "VALUES ('user-2', 'call-1', 0, 'caller', now(), 'k', '\\x00')"
                )
            )

    async def test_no_plaintext_exists_anywhere_in_the_table(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME,
            CallId("call-1"),
            [
                TranscriptEntry(Speaker.CALLER, SECRET_WORDS, later(1)),
                TranscriptEntry(Speaker.AGENT, SECRET_WORDS, later(2)),
            ],
        )

        await assert_nowhere_in(session, "call_transcript_entries", 2, SECRET_WORDS)

    async def test_a_speaker_changed_in_the_database_no_longer_opens(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME, CallId("call-1"), [TranscriptEntry(Speaker.CALLER, "yes, I agree", later(1))]
        )
        await session.execute(text("UPDATE call_transcript_entries SET speaker = 'agent'"))

        with pytest.raises(DecryptionError):
            await transcripts.for_call(ME, CallId("call-1"))

    async def test_a_row_copied_within_its_call_is_refused(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        # "Yes, I agree" said once, made to appear twice.
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME,
            CallId("call-1"),
            [
                TranscriptEntry(Speaker.CALLER, "yes, I agree", later(1)),
                TranscriptEntry(Speaker.AGENT, "noted", later(2)),
            ],
        )
        copy = (
            "INSERT INTO call_transcript_entries "
            "(user_id, call_id, sequence, speaker, said_at, key_id, ciphertext) "
            "SELECT user_id, call_id, {sequence}, speaker, said_at, key_id, ciphertext "
            "FROM call_transcript_entries WHERE sequence = 0"
        )

        with pytest.raises(IntegrityError, match="uq_call_transcript_entries_call_sequence"):
            async with session.begin_nested():
                await session.execute(text(copy.format(sequence="sequence")))
        await session.execute(text(copy.format(sequence="2")))

        with pytest.raises(DecryptionError):
            await transcripts.for_call(ME, CallId("call-1"))

    async def test_a_row_deleted_from_the_middle_is_detected(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME,
            CallId("call-1"),
            [
                TranscriptEntry(Speaker.AGENT, "Shall I tell them you agree?", later(1)),
                TranscriptEntry(Speaker.CALLER, "no", later(2)),
                TranscriptEntry(Speaker.AGENT, "Understood.", later(3)),
            ],
        )
        await session.execute(text("DELETE FROM call_transcript_entries WHERE sequence = 1"))

        with pytest.raises(InvariantError, match="missing"):
            await transcripts.for_call(ME, CallId("call-1"))

    async def test_the_oldest_entries_gone_leave_the_rest_readable(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        # What a purge leaves behind: a call straddling the cutoff loses its first lines.
        await users(session)
        await calls.save(a_call())
        await transcripts.append(
            ME,
            CallId("call-1"),
            [TranscriptEntry(Speaker.CALLER, f"line {i}", later(i)) for i in range(3)],
        )
        await transcripts.append(
            ME, CallId("call-1"), [TranscriptEntry(Speaker.AGENT, "line 3", later(3))]
        )
        await session.execute(text("DELETE FROM call_transcript_entries WHERE sequence < 2"))

        remaining = await transcripts.for_call(ME, CallId("call-1"))

        assert [entry.text for entry in remaining] == ["line 2", "line 3"]

    async def test_ciphertext_moved_onto_another_call_no_longer_opens(
        self, session: AsyncSession, calls: SqlCallRepository, transcripts: SqlTranscriptRepository
    ) -> None:
        await users(session)
        await calls.save(a_call("call-1"))
        await calls.save(a_call("call-2"))
        await transcripts.append(
            ME, CallId("call-1"), [TranscriptEntry(Speaker.CALLER, SECRET_WORDS, later(1))]
        )
        await session.execute(text("UPDATE call_transcript_entries SET call_id = 'call-2'"))

        with pytest.raises(DecryptionError):
            await transcripts.for_call(ME, CallId("call-2"))

    async def test_rotation_reads_old_and_new_and_names_a_removed_key(
        self, session: AsyncSession, calls: SqlCallRepository
    ) -> None:
        await users(session)
        await calls.save(a_call())
        call_id = CallId("call-1")
        await SqlTranscriptRepository(session, AesGcmTranscriptCipher([KEY_A])).append(
            ME, call_id, [TranscriptEntry(Speaker.CALLER, "before rotation", later(1))]
        )

        rotated = SqlTranscriptRepository(session, AesGcmTranscriptCipher([KEY_B, KEY_A]))
        await rotated.append(
            ME, call_id, [TranscriptEntry(Speaker.AGENT, "after rotation", later(2))]
        )

        assert [entry.text for entry in await rotated.for_call(ME, call_id)] == [
            "before rotation",
            "after rotation",
        ]
        key_ids = await session.execute(
            text("SELECT key_id FROM call_transcript_entries ORDER BY said_at")
        )
        assert list(key_ids.scalars()) == ["key-a", "key-b"]

        with pytest.raises(UnknownKeyError) as raised:
            await SqlTranscriptRepository(session, AesGcmTranscriptCipher([KEY_B])).for_call(
                ME, call_id
            )
        assert raised.value.key_id == "key-a"

    @pytest.mark.parametrize("limit", [0, MAX_PURGE_BATCH + 1])
    async def test_purge_batches_are_bounded(self, session: AsyncSession, limit: int) -> None:
        retention = SqlTranscriptRetentionRepository(session)
        with pytest.raises(InvariantError):
            await retention.delete_expired(ME, at_or_before=NOW, limit=limit)
        with pytest.raises(InvariantError):
            await retention.users_with_entries_at_or_before(NOW, after=None, limit=limit)


def a_summary(call_id: str = "call-1") -> CallSummary:
    return CallSummary(
        call_id=CallId(call_id),
        caller=Caller(
            number=CALLER_NUMBER, display_name="Parcel desk", category=CallerCategory.DELIVERY
        ),
        intent=CallIntent.DELIVERY_IN_PROGRESS,
        importance=CallImportance.URGENT,
        outcome=CallOutcome.HANDED_TO_USER,
        headline="A courier needed to know where to leave a parcel",
        started_at=NOW,
        ended_at=later(120),
        human_joined_at=later(30),
        escalation_reason=EscalationReason.DECISION_NEEDS_THE_USER,
        details=(
            ExtractedDetail("reference", "ZX-9043", SECRET_EVIDENCE),
            ExtractedDetail("slot", "afternoon"),
        ),
    )


class TestSummaries:
    async def test_a_full_summary_round_trips(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())
        assert await summaries.get(ME, CallId("call-1")) == a_summary()

    async def test_a_sparse_summary_round_trips(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.REJECTED))
        sparse = CallSummary(
            call_id=CallId("call-1"),
            caller=Caller(),
            intent=CallIntent.UNDETERMINED,
            importance=CallImportance.IGNORABLE,
            outcome=CallOutcome.REJECTED_BY_RULE,
            headline="Rejected by your rules",
            started_at=NOW,
            ended_at=later(1),
        )
        await summaries.add(ME, sparse)
        assert await summaries.get(ME, CallId("call-1")) == sparse

    async def test_a_summary_is_written_once(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())
        with pytest.raises(AlreadyRecordedError):
            await summaries.add(ME, a_summary())

    async def test_one_user_cannot_write_or_read_another_s_summary(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())

        with pytest.raises(RecordNotFoundError):
            await summaries.add(THEM, a_summary())
        assert await summaries.get(THEM, CallId("call-1")) is None
        assert await summaries.get(ME, CallId("no-such-call")) is None

    async def test_what_the_summary_says_is_not_stored_in_clear(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())
        await assert_nowhere_in(session, "call_summaries", 1, SECRET_EVIDENCE)
        await assert_nowhere_in(session, "call_summaries", 1, "where to leave a parcel")

    @pytest.mark.parametrize(
        "change",
        [
            "intent = 'enquiry'",
            "importance = importance - 1",
            "outcome = 'resolved_by_agent'",
            "escalation_reason = NULL",
            "started_at = started_at - interval '1 second'",
            "ended_at = ended_at + interval '1 second'",
            "human_joined_at = NULL",
            "human_joined_at = human_joined_at + interval '1 microsecond'",
        ],
    )
    async def test_a_summary_with_any_column_changed_no_longer_opens(
        self,
        session: AsyncSession,
        calls: SqlCallRepository,
        summaries: SqlSummaryRepository,
        change: str,
    ) -> None:
        # Each of these is what the summary says happened: an urgent call made routine, or an
        # escalation erased, is as much a forgery as a changed headline.
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())
        await session.execute(text(f"UPDATE call_summaries SET {change}"))  # noqa: S608

        with pytest.raises(DecryptionError):
            await summaries.get(ME, CallId("call-1"))

    async def test_a_summary_moved_onto_another_user_no_longer_opens(
        self, session: AsyncSession, calls: SqlCallRepository, summaries: SqlSummaryRepository
    ) -> None:
        await users(session)
        await calls.save(a_call(state=CallState.COMPLETED))
        await calls.save(a_call("call-1-theirs", THEM, state=CallState.COMPLETED))
        await summaries.add(ME, a_summary())
        await session.execute(
            text(
                "UPDATE call_summaries SET call_id = 'call-1-theirs', user_id = 'user-2' "
                "WHERE call_id = 'call-1'"
            )
        )
        with pytest.raises(DecryptionError):
            await summaries.get(THEM, CallId("call-1-theirs"))


async def assert_nowhere_in(session: AsyncSession, table: str, rows: int, words: str) -> None:
    """Scan every column of every row, as text and as raw bytes, for the words.

    Casting the whole row to text covers every column there is now and any added later, so a
    new column that stores the words in clear fails this without anybody updating the test.
    """
    result = await session.execute(text(f"SELECT t::text FROM {table} t"))  # noqa: S608
    rendered = list(result.scalars())
    assert len(rendered) == rows
    needle = words.encode()
    for row in rendered:
        assert words not in row
        assert needle.hex() not in row
    fragments = [words[i : i + 8] for i in range(0, len(words) - 8, 4)]
    for row in rendered:
        for fragment in fragments:
            assert fragment not in row
            assert fragment.encode().hex() not in row
