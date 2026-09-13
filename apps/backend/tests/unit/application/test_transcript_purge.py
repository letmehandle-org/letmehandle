"""The purge's own decisions, without a database: what it records when the store fails."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.retention.purge import (
    PURGE_DELETED,
    PURGE_RUNS,
    PURGE_USER_SKIPPED,
    TranscriptPurge,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.ports.repositories import (
    MAX_PURGE_BATCH,
    PreferencesRepository,
    TranscriptRetentionRepository,
)
from tests.contracts.fakes import FixedClock
from tests.contracts.preference_fakes import InMemoryPreferencesRepository
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.models.preferences import UserPreferences

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)


class BrokenAfterOneBatch(TranscriptRetentionRepository):
    """Deletes one full batch, then fails the way a lost connection does."""

    def __init__(self) -> None:
        self.calls = 0

    async def users_with_entries_at_or_before(
        self, cutoff: datetime, *, after: UserId | None, limit: int
    ) -> list[UserId]:
        return [UserId("user-1")]

    async def delete_expired(self, user_id: UserId, *, at_or_before: datetime, limit: int) -> int:
        self.calls += 1
        if self.calls > 1:
            raise ConnectionError("the database went away")
        return limit


@dataclass
class Scope:
    retention: TranscriptRetentionRepository
    preferences: PreferencesRepository


class DeletesEverything(TranscriptRetentionRepository):
    """Three users with one expired entry each, and a record of whose were deleted."""

    def __init__(self) -> None:
        self.deleted_for: list[str] = []

    async def users_with_entries_at_or_before(
        self, cutoff: datetime, *, after: UserId | None, limit: int
    ) -> list[UserId]:
        names = ("user-1", "user-2", "user-3")
        return [UserId(name) for name in names if after is None or name > after.value][:limit]

    async def delete_expired(self, user_id: UserId, *, at_or_before: datetime, limit: int) -> int:
        self.deleted_for.append(user_id.value)
        return 1


class UnreadableFor(InMemoryPreferencesRepository):
    """Fails to read one user's preferences with an error that is not the domain's own."""

    def __init__(self, user: str, error: Exception) -> None:
        super().__init__()
        self._user = user
        self._error = error

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        if user_id.value == self._user:
            raise self._error
        return await super().get(user_id, for_update=for_update)


def purge_over(
    retention: TranscriptRetentionRepository,
    metrics: RecordingMetrics,
    preferences: PreferencesRepository | None = None,
) -> TranscriptPurge:
    chosen = preferences or InMemoryPreferencesRepository()

    @asynccontextmanager
    async def open_scope() -> AsyncIterator[Scope]:
        yield Scope(retention, chosen)

    return TranscriptPurge(
        open_scope=open_scope, clock=FixedClock(NOW), metrics=metrics, batch_size=3
    )


async def test_a_failure_is_recorded_with_what_was_done_and_then_raised() -> None:
    metrics = RecordingMetrics()
    with pytest.raises(ConnectionError):
        await purge_over(BrokenAfterOneBatch(), metrics).run()
    assert metrics.counted(PURGE_RUNS, outcome="failed") == 1
    assert metrics.observed(PURGE_DELETED) == [3]


@pytest.mark.parametrize("batch_size", [0, MAX_PURGE_BATCH + 1])
def test_a_batch_outside_what_the_store_accepts_is_refused_before_anything_runs(
    batch_size: int,
) -> None:
    # Refused when the purge is built, rather than by the store's first statement once a
    # scheduled run is already under way.
    with pytest.raises(InvariantError):
        TranscriptPurge(
            open_scope=None,  # type: ignore[arg-type]  # never reached: the size is refused first
            clock=FixedClock(NOW),
            metrics=RecordingMetrics(),
            batch_size=batch_size,
        )


@pytest.mark.parametrize(
    "error", [AttributeError("x"), TypeError("x"), ValueError("x"), KeyError("x")], ids=repr
)
async def test_any_failure_to_read_one_user_s_preferences_skips_only_that_user(
    error: Exception,
) -> None:
    retention = DeletesEverything()
    metrics = RecordingMetrics()

    result = await purge_over(retention, metrics, UnreadableFor("user-1", error)).run()

    assert retention.deleted_for == ["user-2", "user-3"]
    assert (result.users_examined, result.users_skipped, result.entries_deleted) == (3, 1, 2)
    assert not result.is_complete
    assert metrics.counted(PURGE_USER_SKIPPED, kind="unreadable_preferences") == 1
    assert metrics.counted(PURGE_RUNS, outcome="incomplete") == 1
