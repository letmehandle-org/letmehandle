"""The purge, against a real database, committing for real.

Each test is one way a purge goes wrong without anybody noticing: deleting a moment too early or
too late, deleting a summary, deleting somebody else's words, honouring the wrong user's setting,
or two purges tripping over each other.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
    SqlTranscriptRetentionRepository,
)
from letmehandle.adapters.database.repositories import SqlPreferencesRepository, SqlUserRepository
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.adapters.security.transcript_cipher import AesGcmTranscriptCipher
from letmehandle.application.retention.purge import (
    DEFAULT_BATCH_SIZE,
    PURGE_DELETED,
    PURGE_RUNS,
    PURGE_SKIPPED,
    PURGE_USER_SKIPPED,
    PURGE_USERS,
    PurgeResult,
)
from letmehandle.domain.models.call import CallSession, Speaker, TranscriptEntry
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.summary import CallOutcome, CallSummary
from letmehandle.domain.models.user import User
from letmehandle.purge import purge_transcripts
from tests.contracts.fakes import FixedClock
from tests.support.config import make_settings
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
KEY = ("key-a", bytes(range(32)))
CIPHER = AesGcmTranscriptCipher([KEY])

ALICE = UserId("alice")
BOB = UserId("bob")
CAROL = UserId("carol")
NUMBERS = {ALICE: "+12025550151", BOB: "+12025550152", CAROL: "+12025550153"}


def ago(days: float = 0, *, microseconds: int = 0) -> datetime:
    return NOW - timedelta(days=days) + timedelta(microseconds=microseconds)


@pytest.fixture
async def engine(
    session: AsyncSession, database_url: str, schema: str
) -> AsyncIterator[AsyncEngine]:
    """An engine on the test's schema whose transactions commit, as the purge's do.

    Depends on `session` for the schema, the tables and the skip when there is no database.
    """
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()


class World:
    """Seeds committed rows and reads them back, the way a running service would have left them."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.factory = create_session_factory(engine)
        self.clock = FixedClock(NOW)

    async def user(self, user_id: UserId, *, retention_days: int | None = None) -> None:
        async with unit_of_work(self.factory) as session:
            await SqlUserRepository(session, self.clock).add(
                User(id=user_id, phone_number=PhoneNumber.parse(NUMBERS[user_id]))
            )
            if retention_days is not None:
                await SqlPreferencesRepository(session, self.clock).save(
                    user_id, UserPreferences(transcript_retention_days=retention_days)
                )

    async def call(self, user_id: UserId, call_id: str, said: list[datetime]) -> None:
        async with unit_of_work(self.factory) as session:
            started = min([*said, NOW])
            call = CallSession.restore(
                id=CallId(call_id),
                user_id=user_id,
                caller=Caller(),
                started_at=started,
                state=CallState.COMPLETED,
                participants=(),
                ended_at=NOW,
            )
            await SqlCallRepository(session, CIPHER, self.clock).save(call)
            await SqlTranscriptRepository(session, CIPHER).append(
                user_id,
                call.id,
                [
                    TranscriptEntry(Speaker.CALLER, f"line said at {moment.isoformat()}", moment)
                    for moment in said
                ],
            )
            await SqlSummaryRepository(session, CIPHER, self.clock).add(
                user_id,
                CallSummary(
                    call_id=call.id,
                    caller=Caller(),
                    intent=CallIntent.ENQUIRY,
                    importance=CallImportance.ROUTINE,
                    outcome=CallOutcome.RESOLVED_BY_AGENT,
                    headline="An enquiry, answered",
                    started_at=started,
                    ended_at=NOW,
                ),
            )

    async def remaining(self, user_id: UserId, call_id: str) -> list[datetime]:
        async with unit_of_work(self.factory) as session:
            entries = await SqlTranscriptRepository(session, CIPHER).for_call(
                user_id, CallId(call_id)
            )
        return [entry.at_instant for entry in entries]

    async def summary_exists(self, user_id: UserId, call_id: str) -> bool:
        async with unit_of_work(self.factory) as session:
            found = await SqlSummaryRepository(session, CIPHER, self.clock).get(
                user_id, CallId(call_id)
            )
        return found is not None

    async def count(self) -> int:
        async with unit_of_work(self.factory) as session:
            result = await session.execute(text("SELECT count(*) FROM call_transcript_entries"))
            return int(result.scalar_one())


async def purge(
    engine: AsyncEngine,
    *,
    metrics: RecordingMetrics | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> PurgeResult:
    """The purge exactly as the scheduled command runs it, on this test's schema."""
    return await purge_transcripts(
        make_settings(),
        engine=engine,
        clock=FixedClock(NOW),
        metrics=metrics or RecordingMetrics(),
        batch_size=batch_size,
    )


class TestWhatIsDeleted:
    async def test_the_boundary_is_the_exact_retention_instant(self, engine: AsyncEngine) -> None:
        world = World(engine)
        await world.user(ALICE)  # no preferences stored: the seven-day default
        exactly = ago(7)
        just_inside = ago(7, microseconds=1)
        await world.call(ALICE, "call-1", [ago(8), exactly, just_inside, ago(0)])

        result = await purge(engine)

        assert await world.remaining(ALICE, "call-1") == [just_inside, ago(0)]
        assert result.entries_deleted == 2
        assert result.is_complete

    async def test_summaries_and_calls_are_never_touched(self, engine: AsyncEngine) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(30), ago(20)])

        await purge(engine)

        assert await world.remaining(ALICE, "call-1") == []
        assert await world.summary_exists(ALICE, "call-1")
        async with unit_of_work(world.factory) as session:
            assert await SqlCallRepository(session, CIPHER, world.clock).get(
                ALICE, CallId("call-1")
            )

    async def test_each_user_s_own_retention_decides_and_nobody_else_s(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE, retention_days=1)
        await world.user(BOB, retention_days=30)
        await world.user(CAROL)
        for user in (ALICE, BOB, CAROL):
            await world.call(user, f"call-{user.value}", [ago(31), ago(10), ago(2), ago(0.5)])

        result = await purge(engine)

        assert await world.remaining(ALICE, "call-alice") == [ago(0.5)]
        assert await world.remaining(BOB, "call-bob") == [ago(10), ago(2), ago(0.5)]
        assert await world.remaining(CAROL, "call-carol") == [ago(2), ago(0.5)]
        assert result.entries_deleted == 3 + 1 + 2
        assert result.users_examined == 3

    async def test_a_shortened_retention_applies_to_what_is_already_stored(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE, retention_days=90)
        await world.call(ALICE, "call-1", [ago(5)])
        await purge(engine)
        assert await world.remaining(ALICE, "call-1") == [ago(5)]

        async with unit_of_work(world.factory) as session:
            await SqlPreferencesRepository(session, world.clock).save(
                ALICE, UserPreferences(transcript_retention_days=1)
            )
        await purge(engine)
        assert await world.remaining(ALICE, "call-1") == []

    async def test_an_expired_line_appended_after_a_kept_one_waits_for_it(
        self, engine: AsyncEngine
    ) -> None:
        # Lines are numbered in the order they were written, which is not always the order they
        # were said. Deleting an expired line from between two kept ones would leave a gap that
        # makes the whole transcript unreadable; it goes when the line before it does.
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(1), ago(10), ago(0.5)])
        await world.call(ALICE, "call-2", [ago(10), ago(9), ago(1)])

        result = await purge(engine)

        assert await world.remaining(ALICE, "call-1") == [ago(10), ago(1), ago(0.5)]
        assert await world.remaining(ALICE, "call-2") == [ago(1)]
        assert result.entries_deleted == 2

    async def test_running_it_again_changes_nothing(self, engine: AsyncEngine) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(9), ago(1)])

        first = await purge(engine)
        second = await purge(engine)

        assert (first.entries_deleted, second.entries_deleted) == (1, 0)
        assert await world.remaining(ALICE, "call-1") == [ago(1)]

    async def test_nothing_to_purge_is_a_completed_run(self, engine: AsyncEngine) -> None:
        metrics = RecordingMetrics()
        result = await purge(engine, metrics=metrics)
        assert result == PurgeResult(0, 0, 0, 0)
        assert metrics.counted(PURGE_RUNS, outcome="completed") == 1


class TestBatches:
    async def test_many_users_and_entries_are_purged_in_small_batches(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        for user in (ALICE, BOB, CAROL):
            await world.user(user)
            await world.call(user, f"call-{user.value}", [ago(8 + i) for i in range(5)] + [ago(0)])

        result = await purge(engine, batch_size=2)

        assert result.entries_deleted == 15
        # Five expired each, two at a time: 2 + 2 + 1, three statements per user.
        assert result.batches == 9
        assert result.users_examined == 3
        assert await world.count() == 3


class TestConcurrency:
    async def test_two_purges_at_once_delete_each_row_once_and_never_error(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        expired_per_user = 60
        users = (ALICE, BOB, CAROL)
        for user in users:
            await world.user(user)

        # Repeated with fresh rows each round, because an interleaving that happens to be kind
        # once proves little.
        for round_number in range(4):
            for user in users:
                await world.call(
                    user,
                    f"call-{user.value}-{round_number}",
                    [ago(8, microseconds=-i) for i in range(expired_per_user)] + [ago(0)],
                )

            results = await asyncio.gather(
                purge(engine, batch_size=7), purge(engine, batch_size=11)
            )

            assert all(result.is_complete for result in results)
            # Every expired row deleted, and each counted by exactly one of the two.
            assert sum(result.entries_deleted for result in results) == expired_per_user * 3
            assert await world.count() == 3 * (round_number + 1)

    async def test_a_purge_skips_rows_another_holds_instead_of_waiting_for_them(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(8, microseconds=-i) for i in range(30)])
        factory = create_session_factory(engine)

        async with factory() as first, factory() as second:
            # The second must not wait: if it did, this timeout would fail the statement.
            await second.execute(text("SET LOCAL statement_timeout = '2s'"))
            held = await SqlTranscriptRetentionRepository(first).delete_expired(
                ALICE, at_or_before=ago(7), limit=10
            )
            rest = await SqlTranscriptRetentionRepository(second).delete_expired(
                ALICE, at_or_before=ago(7), limit=100
            )
            await second.commit()
            await first.commit()

        assert (held, rest) == (10, 20)
        assert await world.count() == 0

    async def test_a_purge_that_rolls_back_leaves_its_rows_for_the_next(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(9), ago(8)])
        factory = create_session_factory(engine)

        async with factory() as session:
            deleted = await SqlTranscriptRetentionRepository(session).delete_expired(
                ALICE, at_or_before=ago(7), limit=10
            )
            await session.rollback()

        assert deleted == 2
        assert await world.count() == 2
        assert (await purge(engine)).entries_deleted == 2


class TestObservability:
    async def test_counts_are_recorded_and_nothing_else(self, engine: AsyncEngine) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.call(ALICE, "call-1", [ago(9), ago(8), ago(1)])
        metrics = RecordingMetrics()

        await purge(engine, metrics=metrics)

        assert metrics.counted(PURGE_RUNS, outcome="completed") == 1
        assert metrics.observed(PURGE_DELETED) == [2]
        assert metrics.observed(PURGE_USERS) == [1]
        assert metrics.observed(PURGE_SKIPPED) == [0]
        assert all(set(labels) <= {"outcome"} for labels in metrics.all_labels())

    async def test_unreadable_preferences_skip_that_user_and_nobody_else(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE)
        await world.user(BOB)
        await world.call(ALICE, "call-alice", [ago(60)])
        await world.call(BOB, "call-bob", [ago(60)])
        async with unit_of_work(world.factory) as session:
            await SqlPreferencesRepository(session, world.clock).save(ALICE, UserPreferences())
            await session.execute(
                text(
                    "UPDATE user_preferences SET document = jsonb_set(document, "
                    "'{transcript_retention_days}', '\"a week\"') WHERE user_id = 'alice'"
                )
            )
        metrics = RecordingMetrics()

        result = await purge(engine, metrics=metrics)

        assert await world.remaining(ALICE, "call-alice") == [ago(60)]
        assert await world.remaining(BOB, "call-bob") == []
        assert (result.users_skipped, result.entries_deleted) == (1, 1)
        assert not result.is_complete
        assert metrics.counted(PURGE_RUNS, outcome="incomplete") == 1

    async def test_a_retention_above_this_version_s_ceiling_skips_that_user(
        self, engine: AsyncEngine
    ) -> None:
        world = World(engine)
        await world.user(ALICE, retention_days=7)
        await world.user(BOB, retention_days=7)
        await world.call(ALICE, "call-alice", [ago(100)])
        await world.call(BOB, "call-bob", [ago(100)])
        async with unit_of_work(world.factory) as session:
            await session.execute(
                text(
                    "UPDATE user_preferences SET document = jsonb_set(document, "
                    "'{transcript_retention_days}', '365') WHERE user_id = 'alice'"
                )
            )
        metrics = RecordingMetrics()

        result = await purge(engine, metrics=metrics)

        assert await world.remaining(ALICE, "call-alice") == [ago(100)]
        assert await world.remaining(BOB, "call-bob") == []
        assert (result.users_skipped, result.entries_deleted) == (1, 1)
        assert metrics.counted(PURGE_USER_SKIPPED, kind="retention_beyond_ceiling") == 1

    @pytest.mark.parametrize(
        "corruption",
        [
            {"notifications": "yes"},
            {"rules": []},
            {"voice": "abc"},
            {"topics": [1]},
            {"version": "x"},
            {"authority": 5},
        ],
        ids=repr,
    )
    async def test_a_malformed_document_sorting_first_stops_nobody_else_s_purge(
        self, engine: AsyncEngine, corruption: dict[str, object]
    ) -> None:
        world = World(engine)
        for user in (ALICE, BOB, CAROL):
            await world.user(user, retention_days=7)
            await world.call(user, f"call-{user.value}", [ago(60)])
        async with unit_of_work(world.factory) as session:
            await session.execute(
                text(
                    "UPDATE user_preferences SET document = document || CAST(:patch AS jsonb) "
                    "WHERE user_id = 'alice'"
                ),
                {"patch": json.dumps(corruption)},
            )
        metrics = RecordingMetrics()

        result = await purge(engine, metrics=metrics, batch_size=1)

        assert await world.remaining(ALICE, "call-alice") == [ago(60)]
        assert await world.remaining(BOB, "call-bob") == []
        assert await world.remaining(CAROL, "call-carol") == []
        assert (result.users_examined, result.users_skipped, result.entries_deleted) == (3, 1, 2)
        assert metrics.counted(PURGE_RUNS, outcome="incomplete") == 1
