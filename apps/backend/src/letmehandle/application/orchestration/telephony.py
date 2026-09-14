"""What a run asks of its call's transport: bounded, through the circuit, and counted."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.orchestration.metrics import PROVIDER_FAILED, PROVIDER_SECONDS
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.application.resilience.retry import RetryPolicy, retry_idempotent
from letmehandle.application.resilience.timing import Stopwatch, within
from letmehandle.domain.errors import DomainError
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from letmehandle.application.orchestration.run import RunContext
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.call_transport import (
        CallTransport,
        SupportsAnswering,
        SupportsBridging,
    )

logger = get_logger(__name__)

# Ending a call is safe to ask twice, so a failure that may pass is asked again.
TERMINATE_RETRY: Final = RetryPolicy(attempts=3)


class CallTelephony:
    """One call's requests to its transport, each answered with whether it was done."""

    def __init__(
        self,
        call_id: CallId,
        transport: CallTransport,
        context: RunContext,
        *,
        note: Callable[[MarkKind, str], None],
    ) -> None:
        self._call_id = call_id
        self._transport = transport
        self._context = context
        self._note = note

    async def answer(self, answering: SupportsAnswering) -> bool:
        return await self._ask("answer", lambda: answering.answer(self._call_id))

    async def dial(self, bridge: SupportsBridging, number: PhoneNumber) -> bool:
        """Ring `number` into the call; never retried, since a second dial rings the phone twice."""
        return await self._ask("dial", lambda: bridge.add_participant(self._call_id, number))

    async def cancel(self, bridge: SupportsBridging, number: PhoneNumber) -> bool:
        return await self._ask("cancel", lambda: bridge.remove_participant(self._call_id, number))

    async def terminate(self) -> bool:
        return await self._ask(
            "terminate", lambda: self._transport.terminate(self._call_id), retry=TERMINATE_RETRY
        )

    async def _ask(
        self,
        stage: str,
        work: Callable[[], Awaitable[None]],
        *,
        retry: RetryPolicy | None = None,
    ) -> bool:
        context = self._context
        circuit = context.circuits[Dependency.TELEPHONY]

        async def attempt() -> None:
            await circuit.call(lambda: within(context.bounds.provider, work))

        stopwatch = Stopwatch()
        try:
            with context.tracer.span(f"telephony.{stage}", dependency=Dependency.TELEPHONY.value):
                if retry is None:
                    await attempt()
                else:
                    await retry_idempotent(attempt, policy=retry)
        except (TimeoutError, DomainError) as error:
            failure = classify(error)
            log_failure(logger, "call.provider_failed", error, stage=stage)
            context.metrics.increment(PROVIDER_FAILED, {"stage": stage, "kind": failure.kind})
            self._note(MarkKind.FAILURE, f"{stage}.{failure.kind}")
            if failure.kind is not FailureKind.CIRCUIT_OPEN:
                context.metrics.observe(PROVIDER_SECONDS, stopwatch.seconds, {"stage": stage})
            return False
        context.metrics.observe(PROVIDER_SECONDS, stopwatch.seconds, {"stage": stage})
        return True
