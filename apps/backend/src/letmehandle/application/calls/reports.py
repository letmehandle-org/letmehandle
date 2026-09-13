"""Accepting a handset's reports: scoped to its user, repeats inert, late reports stored only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from letmehandle.application.orchestration.ports import CallOwnership
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind
from letmehandle.observability.logging import correlation_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.ports.rate_limit import RateLimiter
    from letmehandle.domain.ports.reported_calls import (
        CallEventSink,
        CallReport,
        CallReportRepository,
    )


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """What became of a batch, by the handset's own event identifiers."""

    accepted: tuple[EventId, ...]
    duplicates: tuple[EventId, ...]


class ReportingRateLimitedError(Exception):
    """A handset reporting more often than any handset needs to. Carries when to try again."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("too many reports; try again shortly")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class ReportingPolicy:
    """How often one account's handset may report."""

    requests_per_window: int = 30
    window: timedelta = timedelta(minutes=1)


def scoped_call_id(user_id: UserId, call_id: CallId) -> CallId:
    """The call as the rest of the product knows it: this user's call with this identifier."""
    return CallId(f"{user_id.value}:{call_id.value}")


def scoped_event_id(user_id: UserId, event_id: EventId) -> EventId:
    """The event as the rest of the product knows it, for the same reason as the call."""
    return EventId(f"{user_id.value}:{event_id.value}")


class ReportedCallOwnership(CallOwnership):
    """A reported call belongs to the account named in its scoped identifier."""

    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        owner, separator, _ = incoming.call_id.value.partition(":")
        if not separator:
            return None
        try:
            return UserId(owner)
        except InvariantError:
            return None


class CallReporting:
    """Store a handset's reports and hand on the ones that are new and still current."""

    def __init__(
        self,
        reports: CallReportRepository,
        sink: CallEventSink,
        rate_limiter: RateLimiter,
        policy: ReportingPolicy | None = None,
    ) -> None:
        self._reports = reports
        self._sink = sink
        self._rate_limiter = rate_limiter
        self._policy = policy or ReportingPolicy()

    async def report(self, user_id: UserId, batch: Sequence[CallReport]) -> ReportOutcome:
        decision = await self._rate_limiter.check(
            f"call-reports:{user_id.value}",
            limit=self._policy.requests_per_window,
            window=self._policy.window,
        )
        if not decision.allowed:
            raise ReportingRateLimitedError(decision.retry_after_seconds)
        accepted: list[EventId] = []
        duplicates: list[EventId] = []
        for report in batch:
            # Asked before recording, so this report does not answer it.
            superseded = report.kind is not CallEventKind.ENDED and await self._reports.has_ended(
                user_id, report.call_id
            )
            if not await self._reports.record(user_id, report):
                duplicates.append(report.event_id)
                continue
            accepted.append(report.event_id)
            if not superseded:
                await self._sink.publish(user_id, _to_event(user_id, report))
        return ReportOutcome(accepted=tuple(accepted), duplicates=tuple(duplicates))


def _to_event(user_id: UserId, report: CallReport) -> CallEvent:
    return CallEvent(
        kind=report.kind,
        call_id=scoped_call_id(user_id, report.call_id),
        event_id=scoped_event_id(user_id, report.event_id),
        # Only the number: the handset has no contact name or category.
        caller=None if report.caller_number is None else Caller(number=report.caller_number),
        detail=None if report.ending is None else report.ending.value,
        screening=report.screening,
        occurred_at=report.occurred_at,
        correlation_id=correlation_id.get(),
    )
