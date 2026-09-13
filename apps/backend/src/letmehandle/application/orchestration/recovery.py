"""Ending the calls a stopped process left behind.

A live call is not resumable: its audio stream and its speech session went with the process that
held them. So a call found unfinished at startup is ended at its transport where that is possible,
moved to FAILED and summarised, and never left in a state nothing will move it out of (D-029).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from letmehandle.application.orchestration.ledger import CallLedger
from letmehandle.application.orchestration.summary import Findings, summary_of
from letmehandle.application.preferences.context import DEFAULT_LOCALE
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.ports import Bounds, OpenCallStores
    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.ports.call_transport import CallTransport
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

RECOVERED: Final = "call.recovered"


class Recovery:
    """Finds every unfinished call and ends it as failed."""

    def __init__(
        self,
        *,
        transport: CallTransport,
        stores: OpenCallStores,
        dispatcher: EscalationDispatcher,
        clock: Clock,
        metrics: MetricsRecorder,
        bounds: Bounds,
    ) -> None:
        self._transport = transport
        self._stores = stores
        self._dispatcher = dispatcher
        self._clock = clock
        self._metrics = metrics
        self._bounds = bounds

    async def end_unfinished(self) -> int:
        """End every call left unfinished, a page at a time, and say how many were ended.

        Stops when a page comes back the same as the last, which is storage refusing the writes
        that would have moved those calls on: the next start tries them again.
        """
        ended = 0
        previous: tuple[str, ...] = ()
        while True:
            page = await self._unfinished()
            identifiers = tuple(call.id.value for call in page)
            if not page or identifiers == previous:
                return ended
            previous = identifiers
            for call in page:
                await self._end(call)
            ended += len(page)

    async def _unfinished(self) -> tuple[CallSession, ...]:
        try:
            async with asyncio.timeout(self._bounds.storage.total_seconds()):
                async with self._stores() as stores:
                    return await stores.calls.unfinished(limit=MAX_CALL_PAGE)
        # A process that cannot read its calls still starts: the calls stay unfinished in storage,
        # and the next start tries again. Logged and counted by kind, never raised into startup.
        except Exception as error:  # noqa: BLE001
            logger.error("call.recovery_unavailable", error=type(error).__name__)  # noqa: TRY400
            self._metrics.increment(RECOVERED, {"outcome": "unavailable"})
            return ()

    async def _end(self, call: CallSession) -> None:
        try:
            async with asyncio.timeout(self._bounds.provider.total_seconds()):
                await self._transport.terminate(call.id)
        # Ended here whether or not the transport could let it go: the call is over either way,
        # because nothing holds it any more.
        except Exception as error:  # noqa: BLE001
            logger.warning("call.recovery_terminate_failed", error=type(error).__name__)
        ledger = CallLedger(
            call, stores=self._stores, clock=self._clock, bounds=self._bounds, metrics=self._metrics
        )
        await ledger.move(CallState.FAILED)
        summary = summary_of(call, Findings(), locale=await self._locale(call))
        await ledger.summarised(summary)
        await self._dispatcher.call_ended(call.user_id, call.id, summary.ended_at)
        self._metrics.increment(RECOVERED, {"outcome": "failed"})

    async def _locale(self, call: CallSession) -> str:
        try:
            async with asyncio.timeout(self._bounds.storage.total_seconds()):
                async with self._stores() as stores:
                    preferences = await stores.preferences.get(call.user_id)
        # The summary is written in the default language rather than not at all.
        except Exception:  # noqa: BLE001
            return DEFAULT_LOCALE
        return DEFAULT_LOCALE if preferences is None else preferences.locale
