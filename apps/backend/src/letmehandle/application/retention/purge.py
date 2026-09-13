"""Deleting transcript entries said at least their owner's current retention ago (D-014)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Protocol

from letmehandle.domain.models.preferences import (
    TRANSCRIPT_RETENTION_FLOOR_DAYS,
    UserPreferences,
)
from letmehandle.domain.ports.repositories import MAX_PURGE_BATCH, check_page_size
from letmehandle.observability import catalogue

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.repositories import (
        PreferencesRepository,
        TranscriptRetentionRepository,
    )

PURGE_RUNS: Final = catalogue.count(
    "transcripts.purge.runs", outcome={"completed", "incomplete", "failed"}
)
PURGE_DELETED: Final = catalogue.measure("transcripts.purge.deleted_entries")
PURGE_USERS: Final = catalogue.measure("transcripts.purge.users_examined")
PURGE_SKIPPED: Final = catalogue.measure("transcripts.purge.users_skipped")
# One count per user passed over, labelled with why.
PURGE_USER_SKIPPED: Final = catalogue.count(
    "transcripts.purge.user_skipped", kind={"unreadable_preferences", "retention_beyond_ceiling"}
)

DEFAULT_BATCH_SIZE: Final = 500


class PurgeScope(Protocol):
    """What one short transaction of the purge can reach."""

    retention: TranscriptRetentionRepository
    preferences: PreferencesRepository


# Opens a scope that commits when it closes, one per batch.
type OpenScope = Callable[[], AbstractAsyncContextManager[PurgeScope]]


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Counts, and only counts. Nothing here identifies a person or repeats what they said."""

    users_examined: int
    users_skipped: int
    entries_deleted: int
    batches: int

    @property
    def is_complete(self) -> bool:
        return self.users_skipped == 0


class TranscriptPurge:
    """Deletes every expired transcript entry, for every user, in bounded batches."""

    def __init__(
        self,
        *,
        open_scope: OpenScope,
        clock: Clock,
        metrics: MetricsRecorder,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        # Checked when built, not part-way into a run.
        check_page_size(batch_size, MAX_PURGE_BATCH)
        self._open_scope = open_scope
        self._clock = clock
        self._metrics = metrics
        self._batch_size = batch_size

    async def run(self) -> PurgeResult:
        """Purge once against one instant, and say how much went."""
        now = self._clock.now()
        # Nothing said after this can be expired under any retention a user is allowed.
        nobody_keeps_less = now - timedelta(days=TRANSCRIPT_RETENTION_FLOOR_DAYS)
        counts = _Counts()
        try:
            after: UserId | None = None
            while True:
                async with self._open_scope() as scope:
                    page = await scope.retention.users_with_entries_at_or_before(
                        nobody_keeps_less, after=after, limit=self._batch_size
                    )
                for user_id in page:
                    await self._purge_user(user_id, now, counts)
                if len(page) < self._batch_size:
                    break
                after = page[-1]
        except Exception:
            self._record(counts, outcome="failed")
            raise
        result = counts.result()
        self._record(counts, outcome="completed" if result.is_complete else "incomplete")
        return result

    async def _purge_user(self, user_id: UserId, now: datetime, counts: _Counts) -> None:
        counts.users_examined += 1
        try:
            async with self._open_scope() as scope:
                stored = await scope.preferences.get(user_id)
        except Exception:  # noqa: BLE001 - counted, recorded and reported as incomplete
            # Unreadable preferences leave no retention to honour, so this user waits.
            self._skip(counts, kind="unreadable_preferences")
            return
        preferences = stored or UserPreferences()
        if preferences.retention_exceeds_ceiling:
            # Kept until a deployment that honours the higher retention runs.
            self._skip(counts, kind="retention_beyond_ceiling")
            return
        cutoff = now - timedelta(days=preferences.transcript_retention_days)
        while True:
            async with self._open_scope() as scope:
                deleted = await scope.retention.delete_expired(
                    user_id, at_or_before=cutoff, limit=self._batch_size
                )
            counts.batches += 1
            counts.entries_deleted += deleted
            # A short batch means none are left, or another purge holds the rest.
            if deleted < self._batch_size:
                return

    def _skip(self, counts: _Counts, *, kind: str) -> None:
        counts.users_skipped += 1
        self._metrics.increment(PURGE_USER_SKIPPED, {"kind": kind})

    def _record(self, counts: _Counts, *, outcome: str) -> None:
        self._metrics.increment(PURGE_RUNS, {"outcome": outcome})
        self._metrics.observe(PURGE_DELETED, counts.entries_deleted)
        self._metrics.observe(PURGE_USERS, counts.users_examined)
        self._metrics.observe(PURGE_SKIPPED, counts.users_skipped)


@dataclass(slots=True)
class _Counts:
    users_examined: int = 0
    users_skipped: int = 0
    entries_deleted: int = 0
    batches: int = 0

    def result(self) -> PurgeResult:
        return PurgeResult(
            users_examined=self.users_examined,
            users_skipped=self.users_skipped,
            entries_deleted=self.entries_deleted,
            batches=self.batches,
        )
