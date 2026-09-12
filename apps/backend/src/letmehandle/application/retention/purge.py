"""Deleting transcripts that have outlived their owner's retention (D-014).

The rule, stated once: an entry is expired when its owner's retention, as it stands when the
purge runs, has fully elapsed since the moment the entry was said —

    expired  ⇔  said_at + retention ≤ the purge's start

so an entry said exactly one retention ago is deleted, and one said a microsecond later is kept.

Why each entry's own moment rather than the call's end: the words were said at that moment, and
"kept for seven days" is a promise about them. Keying on the call's end would keep the first
minute of a long call for longer than the user chose. The cost is that a call straddling the
cutoff loses its oldest lines first, which for calls lasting minutes against retentions lasting
days is a difference nobody will see.

Why the retention in force at purge time rather than when the entry was written: a user who
shortens their retention expects older transcripts to go now, not in a week; one who lengthens it
keeps whatever has not yet been purged. Either way the setting means what it says today.

What is never touched: summaries, calls, and anything newer than its owner's cutoff. The purge
holds no cipher, so it cannot read what it deletes.

Concurrency. Each batch is its own short transaction, and the store picks its rows with a lock it
skips rather than waits on. Two purges running at once therefore interleave without blocking,
never both delete the same row, and never both count one: every deletion is counted by exactly
the purge whose statement removed it. A purge that finishes early because the other held the
rows it wanted leaves nothing behind that the other will not delete. Running it again changes
nothing, because a deleted row is not there to be deleted twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, Protocol

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.preferences import (
    TRANSCRIPT_RETENTION_FLOOR_DAYS,
    UserPreferences,
)

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

PURGE_RUNS: Final = "transcripts.purge.runs"
PURGE_DELETED: Final = "transcripts.purge.deleted_entries"
PURGE_USERS: Final = "transcripts.purge.users_examined"
PURGE_SKIPPED: Final = "transcripts.purge.users_skipped"
# One count per user passed over, labelled with why and never with who.
PURGE_USER_SKIPPED: Final = "transcripts.purge.user_skipped"

DEFAULT_BATCH_SIZE: Final = 500


class PurgeScope(Protocol):
    """What one short transaction of the purge can reach."""

    retention: TranscriptRetentionRepository
    preferences: PreferencesRepository


# Opens a scope that commits when it closes. A factory rather than one scope, so that each batch
# is its own transaction: locks held briefly, and progress kept if a later batch fails.
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
        if batch_size < 1:
            raise InvariantError("a purge batch deletes at least one entry")
        self._open_scope = open_scope
        self._clock = clock
        self._metrics = metrics
        self._batch_size = batch_size

    async def run(self) -> PurgeResult:
        """Purge once, and say how much went.

        The instant is taken once, at the start, so every user is judged against the same
        moment and a long run does not creep its own cutoff forward while it works.
        """
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
            # Preferences that cannot be read leave no retention to honour. Deleting at the
            # default could destroy what this user chose to keep; so their transcripts wait
            # for a run that can read the setting, the run says it was incomplete, and nobody
            # else's purge is held up behind them.
            #
            # Any failure, not only the domain's own. What one corrupt document raises is not
            # something this loop can enumerate, and one row nobody can read must not be what
            # stops every user sorting after it from ever being purged. A failure that is not
            # about one user, such as the database going away, fails the next statement anyway.
            self._skip(counts, kind="unreadable_preferences")
            return
        retention = (stored or UserPreferences()).transcript_retention_days
        cutoff = now - timedelta(days=retention)
        while True:
            async with self._open_scope() as scope:
                deleted = await scope.retention.delete_expired(
                    user_id, at_or_before=cutoff, limit=self._batch_size
                )
            counts.batches += 1
            counts.entries_deleted += deleted
            # Fewer than a full batch means none are left, or another purge holds the rest and
            # will delete them. The cutoff is in the past, so the set cannot grow while this runs.
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
