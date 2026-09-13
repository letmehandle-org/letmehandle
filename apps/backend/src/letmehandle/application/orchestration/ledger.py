"""The one writer of a call's state.

Everything that changes a call — a move, somebody joining or leaving, a line said, the summary —
goes through here, and is stored as it happens, so a restart finds the call as it last stood. Only
orchestration imports this module, and a test asserts it: the agent, an adapter or a route that
could nudge a call's state would be a second owner of it, which is the failure D-029 exists to
prevent.

A write that fails is logged and counted, never raised. The caller is still on the line, and a
database that did not answer is not a reason to hang up on them; the next write stores the whole
call again, and teardown stores it last.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from letmehandle.application.resilience.retry import RetryPolicy, retry_idempotent
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.models.call import TranscriptEntry
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.timeline import MarkKind, TimelineMark
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from letmehandle.application.orchestration.ports import Bounds, CallStores, OpenCallStores
    from letmehandle.domain.models.call import CallSession, ParticipantRole, Speaker
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

STORAGE_FAILED: Final = catalogue.count(
    "call.storage_failed",
    stage={"open", "move", "join", "leave", "transcript", "final", "summary"},
    kind=FailureKind,
)

# How teardown's final save is tried again, each attempt within the storage bound. More than once,
# because the one write a call cannot do without meeting a connection reset is worth another try,
# and storing the whole call again is safe; few, because a database that is down stays down for
# longer than a teardown should wait.
FINAL_SAVE: Final = RetryPolicy(attempts=3)
TRANSITION: Final = catalogue.count("call.transition", outcome=CallState)
# How long a call spent in the state it has just left, labelled by that state.
STATE_SECONDS: Final = catalogue.measure("call.state_seconds", outcome=CallState)

# How far ahead of this host's clock a reported moment may be and still be believed: the allowance
# a handset's rules snapshot is given for the same two clocks disagreeing (D-028). Further ahead,
# the handset's clock is wrong, and now is the nearest moment that can be true.
REPORTED_CLOCK_SKEW: Final = timedelta(minutes=5)


def reported_instant(
    reported: datetime | None, *, now: datetime, not_before: datetime | None = None
) -> datetime:
    """When something happened to a call: the moment its transport reported, or else now.

    A moment beyond `REPORTED_CLOCK_SKEW` ahead of now is taken as now, and one earlier than
    `not_before` as `not_before`, so a call's history never runs backwards (D-029).
    """
    instant = now if reported is None or reported > now + REPORTED_CLOCK_SKEW else reported
    if not_before is not None and instant < not_before:
        return not_before
    return instant


class CallLedger:
    """One call, changed only by its run, and stored on every change."""

    def __init__(
        self,
        call: CallSession,
        *,
        stores: OpenCallStores,
        clock: Clock,
        bounds: Bounds,
        metrics: MetricsRecorder,
    ) -> None:
        self._call = call
        self._stores = stores
        self._clock = clock
        self._bounds = bounds
        self._metrics = metrics
        self._state_since = call.started_at
        # Marks not yet stored. They go with the call's next save, in its unit of work, so a
        # timeline never holds a mark for a move the stored call has not made.
        self._marks: list[TimelineMark] = []

    @property
    def call(self) -> CallSession:
        return self._call

    @property
    def state(self) -> CallState:
        return self._call.state

    @property
    def state_since(self) -> datetime:
        """When the call entered the state it is in."""
        return self._state_since

    async def opened(self) -> None:
        """Store the call as it arrived."""
        self._marks.append(
            TimelineMark(self._call.started_at, MarkKind.TRANSITION, self.state.value)
        )
        await self._save("open")

    def note(self, kind: MarkKind, name: str) -> None:
        """Mark something that happened to the call now. Stored with the call's next save."""
        self._marks.append(TimelineMark(self._clock.now(), kind, name))

    async def move(self, state: CallState, *, at: datetime | None = None) -> None:
        """Move the call, stamped `at` or now. Raises for a move the state machine forbids.

        `at` is when the event that moved it happened, where its transport said; it is never
        recorded earlier than the move before it.

        Stored at once, except an ending: that is stored with the summary, once teardown has let
        everything go, so a process that stops part-way through a teardown leaves the call
        unfinished for the next start to end rather than ended with no summary.
        """
        left, now = self._call.state, self._instant(at)
        self._call.move_to(state, at_instant=now)
        self._metrics.increment(TRANSITION, {"outcome": state.value})
        self._metrics.observe(
            STATE_SECONDS, (now - self._state_since).total_seconds(), {"outcome": left}
        )
        self._state_since = now
        self._marks.append(TimelineMark(now, MarkKind.TRANSITION, state.value))
        if not self._call.is_over:
            await self._save("move")

    async def joined(self, role: ParticipantRole, *, at: datetime | None = None) -> None:
        """Somebody came on the call, `at` or now. Hearing it twice changes nothing."""
        if self._call.has_participant(role):
            return
        self._call.add_participant(role, self._instant(at))
        await self._save("join")

    async def left(self, role: ParticipantRole, *, at: datetime | None = None) -> None:
        """Somebody left it, `at` or now. Hearing it about somebody not there changes nothing."""
        if not self._call.has_participant(role):
            return
        self._call.remove_participant(role, self._instant(at))
        await self._save("leave")

    async def said(self, speaker: Speaker, text: str) -> None:
        """Keep a line, in memory for the agent and sealed in storage for the user."""
        entry = TranscriptEntry(speaker, text, self._clock.now())
        self._call.record(speaker, text, entry.at_instant)

        async def append(stores: CallStores) -> None:
            await stores.transcripts.append(self._call.user_id, self._call.id, [entry])

        await self._write("transcript", append)

    async def summarised(self, summary: CallSummary) -> None:
        """Store the call as it ended, then its summary, which needs the stored call.

        A call that could not be stored as it ended gets no summary. It stays unfinished in
        storage, and the next start ends and summarises it as the failure it then is; a summary
        written beside it would say the call went one way while its record says another.
        """
        if not await self._save("final", retry=FINAL_SAVE):
            return

        async def add(stores: CallStores) -> None:
            await stores.summaries.add(self._call.user_id, summary)

        await self._write("summary", add)

    def _instant(self, reported: datetime | None) -> datetime:
        return reported_instant(reported, now=self._clock.now(), not_before=self._state_since)

    async def _save(self, stage: str, *, retry: RetryPolicy | None = None) -> bool:
        stored = 0

        async def save(stores: CallStores) -> None:
            nonlocal stored
            marks = tuple(self._marks)
            await stores.calls.save(self._call)
            await stores.timeline.append(self._call.id, marks)
            stored = len(marks)

        saved = await self._write(stage, save, retry=retry)
        if saved:
            # Only those this save stored: a judgement may have noted another while it ran.
            del self._marks[:stored]
        return saved

    async def _write(
        self,
        stage: str,
        work: Callable[[CallStores], Awaitable[None]],
        *,
        retry: RetryPolicy | None = None,
    ) -> bool:
        """Do `work` in one unit of work, within the storage bound, and say whether it was done.

        With `retry`, which only work safe to repeat is given, a failure that may pass is tried
        again. Every failed attempt is counted; giving up is logged once.
        """

        async def attempt() -> None:
            try:
                async with asyncio.timeout(self._bounds.storage.total_seconds()):
                    async with self._stores() as stores:
                        await work(stores)
            except Exception as error:
                kind = classify(error).kind
                self._metrics.increment(STORAGE_FAILED, {"stage": stage, "kind": kind})
                raise

        try:
            if retry is None:
                await attempt()
            else:
                await retry_idempotent(attempt, policy=retry)
        # Broad on purpose, and not swallowed: logged and counted by kind. A storage driver fails in
        # its own terms, a timeout in another, and a live call outlasts every one of them.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.storage_failed", error, stage=stage)
            self.note(MarkKind.FAILURE, f"storage.{stage}.{classify(error).kind}")
            return False
        return True
