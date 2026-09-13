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
from typing import TYPE_CHECKING, Final

from letmehandle.domain.failures import classify
from letmehandle.domain.models.call import TranscriptEntry
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from letmehandle.application.orchestration.ports import Bounds, CallStores, OpenCallStores
    from letmehandle.domain.models.call import CallSession, ParticipantRole, Speaker
    from letmehandle.domain.models.call_state import CallState
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

STORAGE_FAILED: Final = "call.storage_failed"

# How many times teardown's final save is tried, each within the storage bound. More than once,
# because the one write a call cannot do without meeting a connection reset is worth another try;
# few, because a database that is down stays down for longer than a teardown should wait.
FINAL_SAVE_ATTEMPTS: Final = 3
TRANSITION: Final = "call.transition"


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

    @property
    def call(self) -> CallSession:
        return self._call

    @property
    def state(self) -> CallState:
        return self._call.state

    async def opened(self) -> None:
        """Store the call as it arrived."""
        await self._save("open")

    async def move(self, state: CallState) -> None:
        """Move the call, stamped now. Raises for a move the state machine forbids.

        Stored at once, except an ending: that is stored with the summary, once teardown has let
        everything go, so a process that stops part-way through a teardown leaves the call
        unfinished for the next start to end rather than ended with no summary.
        """
        self._call.move_to(state, at_instant=self._clock.now())
        self._metrics.increment(TRANSITION, {"outcome": state.value})
        if not self._call.is_over:
            await self._save("move")

    async def joined(self, role: ParticipantRole) -> None:
        """Somebody came on the call. Hearing it twice changes nothing."""
        if self._call.has_participant(role):
            return
        self._call.add_participant(role, self._clock.now())
        await self._save("join")

    async def left(self, role: ParticipantRole) -> None:
        """Somebody left it. Hearing it about somebody not there changes nothing."""
        if not self._call.has_participant(role):
            return
        self._call.remove_participant(role, self._clock.now())
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
        for _ in range(FINAL_SAVE_ATTEMPTS):
            if await self._save("final"):
                break
        else:
            return

        async def add(stores: CallStores) -> None:
            await stores.summaries.add(self._call.user_id, summary)

        await self._write("summary", add)

    async def _save(self, stage: str) -> bool:
        async def save(stores: CallStores) -> None:
            await stores.calls.save(self._call)

        return await self._write(stage, save)

    async def _write(self, stage: str, work: Callable[[CallStores], Awaitable[None]]) -> bool:
        """Do `work` in one unit of work, within the storage bound, and say whether it was done."""
        try:
            async with asyncio.timeout(self._bounds.storage.total_seconds()):
                async with self._stores() as stores:
                    await work(stores)
        # Broad on purpose, and not swallowed: logged and counted by kind. A storage driver fails in
        # its own terms, a timeout in another, and a live call outlasts every one of them.
        except Exception as error:  # noqa: BLE001
            # Without a traceback: its frames can hold what was said.
            logger.error(  # noqa: TRY400
                "call.storage_failed", stage=stage, error=type(error).__name__
            )
            self._metrics.increment(STORAGE_FAILED, {"stage": stage, "kind": classify(error).kind})
            return False
        return True
