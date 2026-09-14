"""Ending, as failed, every call a stopped process left unfinished, on every line (D-029)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.application.orchestration.ledger import CallLedger
from letmehandle.application.preferences.context import DEFAULT_LOCALE
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.ports import Bounds, OpenCallStores
    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.ports.call_transport import CallTransport
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

RECOVERED: Final = catalogue.count("call.recovered", outcome={"failed", "unavailable"})


class Recovery:
    """Finds every unfinished call and ends it as failed."""

    def __init__(
        self,
        *,
        transports: tuple[CallTransport, ...],
        stores: OpenCallStores,
        dispatcher: EscalationDispatcher,
        clock: Clock,
        metrics: MetricsRecorder,
        bounds: Bounds,
    ) -> None:
        self._transports = transports
        self._stores = stores
        self._dispatcher = dispatcher
        self._clock = clock
        self._metrics = metrics
        self._bounds = bounds

    async def end_unfinished(self) -> int:
        """End every unfinished call a page at a time until a page repeats; say how many."""
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
        # Calls that cannot be read stay unfinished for the next start: logged and counted by kind.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.recovery_unavailable", error)
            self._metrics.increment(RECOVERED, {"outcome": "unavailable"})
            return ()

    async def _end(self, call: CallSession) -> None:
        for transport in self._transports:
            try:
                async with asyncio.timeout(self._bounds.provider.total_seconds()):
                    await transport.terminate(call.id)
            # The call is ended here whether or not a transport let it go.
            except Exception as error:  # noqa: BLE001
                log_failure(logger, "call.recovery_terminate_failed", error)
        ledger = CallLedger(
            call, stores=self._stores, clock=self._clock, bounds=self._bounds, metrics=self._metrics
        )
        await ledger.move(CallState.FAILED)
        summary = fallback_summary(CallFacts(call), locale=await self._locale(call))
        await ledger.summarised(summary)
        await self._dispatcher.call_ended(call.user_id, call.id, summary.ended_at)
        self._metrics.increment(RECOVERED, {"outcome": "failed"})

    async def _locale(self, call: CallSession) -> str:
        try:
            async with asyncio.timeout(self._bounds.storage.total_seconds()):
                async with self._stores() as stores:
                    preferences = await stores.preferences.get(call.user_id)
        # Preferences that cannot be read give the default language.
        except Exception:  # noqa: BLE001
            return DEFAULT_LOCALE
        return DEFAULT_LOCALE if preferences is None else preferences.locale
