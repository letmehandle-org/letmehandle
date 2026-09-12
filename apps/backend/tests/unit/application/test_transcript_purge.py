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
    TranscriptPurge,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.ports.repositories import (
    PreferencesRepository,
    TranscriptRetentionRepository,
)
from tests.contracts.fakes import FixedClock
from tests.contracts.preference_fakes import InMemoryPreferencesRepository
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


def purge_over(
    retention: TranscriptRetentionRepository, metrics: RecordingMetrics
) -> TranscriptPurge:
    @asynccontextmanager
    async def open_scope() -> AsyncIterator[Scope]:
        yield Scope(retention, InMemoryPreferencesRepository())

    return TranscriptPurge(
        open_scope=open_scope, clock=FixedClock(NOW), metrics=metrics, batch_size=3
    )


async def test_a_failure_is_recorded_with_what_was_done_and_then_raised() -> None:
    metrics = RecordingMetrics()
    with pytest.raises(ConnectionError):
        await purge_over(BrokenAfterOneBatch(), metrics).run()
    assert metrics.counted(PURGE_RUNS, outcome="failed") == 1
    assert metrics.observed(PURGE_DELETED) == [3]


def test_a_batch_of_nothing_is_refused() -> None:
    with pytest.raises(InvariantError):
        TranscriptPurge(
            open_scope=None,  # type: ignore[arg-type]
            clock=FixedClock(NOW),
            metrics=RecordingMetrics(),
            batch_size=0,
        )
