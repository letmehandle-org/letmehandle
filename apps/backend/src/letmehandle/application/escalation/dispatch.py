"""Telling the user about an escalation, on every device they have, without ever getting in its way.

The governing rule is D-016: the phone ringing is the escalation, and this is context for it. So
nothing here raises into its caller, nothing waits longer than a short bound, and a failure is a
recorded outcome — on the stored context, where the app surfaces it, and in the metrics — rather
than an exception on the path that is ringing somebody's phone.

In order, for one escalation:

  1. The context is claimed in storage. A second dispatch for the same call finds it claimed and
     stops: one notification per call, however often the caller asks. The context is stored
     before anything is sent, so the app can fetch it even if every push is lost.
  2. Every device the user has is sent to at once, through the provider for its platform, each
     bounded by the same deadline. A platform with no provider configured is an outcome too.
  3. Tokens a platform reported dead are removed, and what became of the notification is
     recorded on the context.

A dispatch started in the background can claim its context after the call it is for has ended,
when storage answers the claim late. The dispatcher remembers when each call ended, so a context
claimed for a call already over is marked ended as soon as it is stored, and `call_ended` never
waits on a notification to find out.

Storage is reached through a scope that opens and commits its own unit of work, so no database
transaction is held open while a push is in flight.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.escalation.notification import DEFAULT_LOCALE, notification_for
from letmehandle.application.resilience.circuit import CircuitOpenError
from letmehandle.application.resilience.timing import Stopwatch
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.models.escalation_context import NotificationDelivery
from letmehandle.domain.ports.notification import DeliveryStatus, DevicePlatform
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from letmehandle.application.resilience.circuit import Circuits
    from letmehandle.domain.models.escalation_context import EscalationContext
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.notification import (
        DeliveryOutcome,
        DeviceToken,
        EscalationNotification,
        NotificationProvider,
    )
    from letmehandle.domain.ports.repositories import (
        DeviceRepository,
        EscalationContextRepository,
    )
    from letmehandle.domain.ports.tracing import Tracer

logger = get_logger(__name__)

# Short, because a notification arriving after the user has already answered is worth little, and
# a caller awaiting this — rather than starting it in the background — waits at most this long.
DEFAULT_TIMEOUT: Final = timedelta(seconds=5)

# How many ended calls are remembered, so a context claimed after its call ended is marked ended.
# Bounded: a process runs for weeks, and a claim arrives within moments of its call or not at all.
REMEMBERED_ENDINGS: Final = 10_000


@dataclass(frozen=True, slots=True)
class EscalationStores:
    """The storage one unit of work gives the dispatcher."""

    devices: DeviceRepository
    contexts: EscalationContextRepository


class DispatchResult(StrEnum):
    """What happened to a dispatch as a whole."""

    SENT = "sent"
    DEDUPLICATED = "deduplicated"
    NO_DEVICES = "no_devices"
    STORAGE_UNAVAILABLE = "storage_unavailable"


class AttemptResult(StrEnum):
    """What happened on one device."""

    DELIVERED = "delivered"
    REJECTED = "rejected"
    TOKEN_INVALID = "token_invalid"  # noqa: S105 - an outcome, not a credential
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERRORED = "errored"
    NOT_CONFIGURED = "not_configured"
    # Not attempted: the platform's push service has been failing, and its circuit is open.
    UNAVAILABLE = "unavailable"


DISPATCH_METRIC: Final = catalogue.count("escalation.dispatch", outcome=DispatchResult)
DELIVERY_METRIC: Final = catalogue.count(
    "escalation.delivery",
    platform=DevicePlatform,
    provider=catalogue.NAMED_IN_CODE,
    outcome=AttemptResult,
)
TOKEN_REMOVED_METRIC: Final = catalogue.count(
    "escalation.token_removed", platform=DevicePlatform, provider=catalogue.NAMED_IN_CODE
)
STORAGE_FAILED: Final = catalogue.count(
    "escalation.storage_failed", stage={"claim", "record", "end"}, kind=FailureKind
)
DELIVERY_SECONDS: Final = catalogue.measure(
    "escalation.delivery_seconds", platform=DevicePlatform, provider=catalogue.NAMED_IN_CODE
)

_FROM_STATUS: Final = {
    DeliveryStatus.DELIVERED: AttemptResult.DELIVERED,
    DeliveryStatus.REJECTED: AttemptResult.REJECTED,
    DeliveryStatus.TOKEN_INVALID: AttemptResult.TOKEN_INVALID,
    DeliveryStatus.FAILED: AttemptResult.FAILED,
}


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    """One device's outcome. The token is kept for cleanup and never printed in full."""

    token: DeviceToken
    result: AttemptResult
    detail: str | None = None
    token_removed: bool = False


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Everything a dispatch did, returned rather than raised."""

    result: DispatchResult
    attempts: tuple[DeliveryAttempt, ...] = ()
    delivery: NotificationDelivery | None = None

    @property
    def delivered(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.result is AttemptResult.DELIVERED)


class EscalationDispatcher:
    """Delivers escalation notifications to a user's devices and records what happened."""

    def __init__(
        self,
        *,
        providers: Iterable[NotificationProvider],
        stores: Callable[[], AbstractAsyncContextManager[EscalationStores]],
        metrics: MetricsRecorder,
        tracer: Tracer,
        circuits: Circuits,
        timeout: timedelta = DEFAULT_TIMEOUT,
        locale: str = DEFAULT_LOCALE,
    ) -> None:
        self._providers: dict[DevicePlatform, NotificationProvider] = {}
        for provider in providers:
            if provider.platform in self._providers:
                raise ValueError(f"two notification providers were given for {provider.platform}")
            self._providers[provider.platform] = provider
        self._stores = stores
        self._metrics = metrics
        self._tracer = tracer
        self._circuits = circuits
        self._timeout = timeout
        self._locale = locale
        self._background: set[asyncio.Task[DispatchReport]] = set()
        self._ended: OrderedDict[tuple[UserId, CallId], datetime] = OrderedDict()

    async def dispatch(self, user_id: UserId, context: EscalationContext) -> DispatchReport:
        """Notify every device this user has about this escalation. Never raises.

        Awaiting it takes at most the timeout plus two short storage round trips. A caller that
        must not wait even that long uses `start`.
        """
        claimed = replace(context, delivery=NotificationDelivery.PENDING)
        try:
            async with self._stores() as stores:
                first = await stores.contexts.claim(user_id, claimed)
                tokens = await stores.devices.tokens_for(user_id) if first else []
        except Exception as error:  # noqa: BLE001 - D-016: recorded, never raised into the escalation
            return self._finish(
                DispatchReport(DispatchResult.STORAGE_UNAVAILABLE), stage="claim", error=error
            )

        if not first:
            return self._finish(DispatchReport(DispatchResult.DEDUPLICATED))
        await self._end_if_over(user_id, claimed.call_id)
        if not tokens:
            report = DispatchReport(
                DispatchResult.NO_DEVICES, delivery=NotificationDelivery.NO_DEVICES
            )
            return await self._record(user_id, claimed.call_id, report, dead=())

        deadline = asyncio.get_running_loop().time() + self._timeout.total_seconds()
        attempts = tuple(
            await asyncio.gather(*(self._attempt(token, claimed, deadline) for token in tokens))
        )
        delivered = any(attempt.result is AttemptResult.DELIVERED for attempt in attempts)
        report = DispatchReport(
            DispatchResult.SENT,
            attempts=attempts,
            delivery=NotificationDelivery.DELIVERED if delivered else NotificationDelivery.FAILED,
        )
        dead = tuple(a.token for a in attempts if a.result is AttemptResult.TOKEN_INVALID)
        return await self._record(user_id, claimed.call_id, report, dead=dead)

    def start(self, user_id: UserId, context: EscalationContext) -> asyncio.Task[DispatchReport]:
        """Dispatch in the background and return at once, so the ring is not delayed at all.

        The task is held here until it finishes: a task nothing references can be collected
        before it runs.
        """
        task = asyncio.get_running_loop().create_task(self.dispatch(user_id, context))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def call_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        """Record that the call is over, so the app shows a summary. Never raises.

        Returns whether a context was marked; false for a call that never escalated, and false
        when storage could not be reached, which is logged and counted.
        """
        # Remembered before marking: a claim committed from here on finds the call over and marks
        # its own context, and one committed before is there for the mark below to find.
        self._ended[(user_id, call_id)] = at_instant
        while len(self._ended) > REMEMBERED_ENDINGS:
            self._ended.popitem(last=False)
        return await self._mark_ended(user_id, call_id, at_instant)

    async def _end_if_over(self, user_id: UserId, call_id: CallId) -> None:
        at_instant = self._ended.get((user_id, call_id))
        if at_instant is not None:
            await self._mark_ended(user_id, call_id, at_instant)

    async def _mark_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        try:
            async with self._stores() as stores:
                return await stores.contexts.mark_ended(user_id, call_id, at_instant)
        except Exception as error:  # noqa: BLE001 - ending a call must not fail on this record
            self._failed("end", error)
            return False

    async def aclose(self) -> None:
        """Let background dispatches finish. Each is already bounded by the timeout."""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    async def _attempt(
        self, token: DeviceToken, context: EscalationContext, deadline: float
    ) -> DeliveryAttempt:
        provider = self._providers.get(token.platform)
        if provider is None:
            attempt = DeliveryAttempt(token, AttemptResult.NOT_CONFIGURED)
            self._metrics.increment(
                DELIVERY_METRIC, {"platform": token.platform.value, "outcome": attempt.result.value}
            )
            return attempt

        labels = {"platform": token.platform.value, "provider": provider.name}
        stopwatch = Stopwatch()
        try:
            notification = notification_for(
                context, fits=_within_limit(provider), locale=self._locale
            )
            with self._tracer.span("notification.delivery", platform=token.platform.value):
                outcome = await self._circuits.push(token.platform).call(
                    lambda: _sent_by(provider, token, notification, deadline),
                    failed_when=_service_failed,
                )
            attempt = DeliveryAttempt(token, _FROM_STATUS[outcome.status], outcome.detail)
        except CircuitOpenError:
            attempt = DeliveryAttempt(token, AttemptResult.UNAVAILABLE)
        except TimeoutError:
            attempt = DeliveryAttempt(token, AttemptResult.TIMED_OUT)
        except Exception as error:  # noqa: BLE001 - one device's defect must not cost the others
            # Logged without a traceback: its frames can hold the notification being sent.
            log_failure(
                logger,
                "escalation.delivery_errored",
                error,
                provider=provider.name,
                device=str(token),
            )
            attempt = DeliveryAttempt(token, AttemptResult.ERRORED, type(error).__name__)

        if attempt.result is not AttemptResult.UNAVAILABLE:
            self._metrics.observe(DELIVERY_SECONDS, stopwatch.seconds, labels)
        self._metrics.increment(DELIVERY_METRIC, {**labels, "outcome": attempt.result.value})
        return attempt

    async def _record(
        self,
        user_id: UserId,
        call_id: CallId,
        report: DispatchReport,
        *,
        dead: tuple[DeviceToken, ...],
    ) -> DispatchReport:
        """Remove dead tokens and record the delivery, in one unit of work after sending."""
        delivery = report.delivery or NotificationDelivery.FAILED
        try:
            async with self._stores() as stores:
                for token in dead:
                    await stores.devices.remove(user_id, token)
                await stores.contexts.record_delivery(user_id, call_id, delivery)
        except Exception as error:  # noqa: BLE001 - the notification went; only the record did not
            return self._finish(report, stage="record", error=error)

        for token in dead:
            provider = self._providers[token.platform]
            self._metrics.increment(
                TOKEN_REMOVED_METRIC, {"platform": token.platform.value, "provider": provider.name}
            )
        removed = set(dead)
        attempts = tuple(
            replace(attempt, token_removed=attempt.token in removed) for attempt in report.attempts
        )
        return self._finish(replace(report, attempts=attempts))

    def _finish(
        self, report: DispatchReport, *, stage: str | None = None, error: Exception | None = None
    ) -> DispatchReport:
        if error is not None and stage is not None:
            self._failed(stage, error)
        self._metrics.increment(DISPATCH_METRIC, {"outcome": report.result.value})
        return report

    def _failed(self, stage: str, error: Exception) -> None:
        log_failure(logger, "escalation.storage_failed", error, stage=stage)
        self._metrics.increment(STORAGE_FAILED, {"stage": stage, "kind": classify(error).kind})


async def _sent_by(
    provider: NotificationProvider,
    token: DeviceToken,
    notification: EscalationNotification,
    deadline: float,
) -> DeliveryOutcome:
    async with asyncio.timeout_at(deadline):
        return await provider.send(token, notification)


def _service_failed(outcome: DeliveryOutcome) -> bool:
    # The platform saying it could not deliver is its service failing, as an unreachable one would
    # be; a device it refuses, or a token it no longer knows, is the platform working.
    return outcome.status is DeliveryStatus.FAILED


def _within_limit(provider: NotificationProvider) -> Callable[[EscalationNotification], bool]:
    def fits(notification: EscalationNotification) -> bool:
        return provider.payload_size(notification) <= provider.payload_limit_bytes

    return fits
