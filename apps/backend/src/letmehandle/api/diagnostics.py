"""Operator diagnostics behind their own token: live calls, call structure, metrics (D-038)."""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from letmehandle.adapters.database.session import unit_of_work
from letmehandle.adapters.database.timeline import SqlCallTimelineRepository
from letmehandle.api.dependencies import container_of
from letmehandle.api.errors import ApiError, database_unavailable
from letmehandle.application.orchestration.run import CallStanding
from letmehandle.bootstrap import Observability
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId

_bearer = HTTPBearer(auto_error=False)


class LiveCall(BaseModel):
    """Where one live call stands."""

    call_id: str
    state: str
    since: datetime
    seconds_in_state: float


class LiveCalls(BaseModel):
    """Every live call, oldest state first."""

    calls: list[LiveCall]


class TimelineMarkView(BaseModel):
    """One moment in a call's life."""

    at: datetime
    kind: str
    name: str


class ParticipantView(BaseModel):
    """Who was on the call, by role, and when."""

    role: str
    joined_at: datetime
    left_at: datetime | None


class EscalationView(BaseModel):
    """Why the user was asked for, and what became of it."""

    reason: str
    status: str
    delivery: str


class CallTimeline(BaseModel):
    """A call as structure: what it was, what it moved through, and where it stands if live."""

    call_id: str
    state: str
    handling: str | None
    started_at: datetime
    ended_at: datetime | None
    escalated_at: datetime | None
    outcome: str | None
    participants: list[ParticipantView]
    escalation: EscalationView | None
    marks: list[TimelineMarkView]
    live: LiveCall | None


class CountView(BaseModel):
    """How often something happened since the process started."""

    metric: str
    labels: dict[str, str]
    count: int


class MeasureView(BaseModel):
    """The spread of recent measurements of one series, in seconds for a latency."""

    metric: str
    labels: dict[str, str]
    count: int
    p50: float
    p90: float
    p99: float
    maximum: float


class MetricsView(BaseModel):
    """Every series this process has recorded, and where each dependency's circuit stands."""

    counts: list[CountView]
    measures: list[MeasureView]
    dependencies: dict[str, str]


def require_diagnostics_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Refuse a request without the diagnostics token, and every request where there is none."""
    configured = request.app.state.settings.diagnostics_token
    if configured is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Not found.")
    given = b"" if credentials is None else credentials.credentials.encode()
    # Compared in constant time.
    if not hmac.compare_digest(given, configured.get_secret_value().encode()):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "not_authenticated",
            "This request needs the diagnostics token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


router = APIRouter(
    prefix="/diagnostics",
    tags=["diagnostics"],
    include_in_schema=False,
    dependencies=[Depends(require_diagnostics_token)],
)


@router.get("/calls", response_model=LiveCalls)
async def live_calls(request: Request) -> LiveCalls:
    """Every live call in this process, oldest state first."""
    return LiveCalls(calls=[_live(request, each) for each in _standings(request)])


@router.get("/calls/{call_id}", response_model=CallTimeline)
async def call_timeline(request: Request, call_id: str) -> CallTimeline:
    """One call's stored outline and timeline, and where it stands if this process holds it."""
    factory = request.app.state.session_factory
    if factory is None:
        raise database_unavailable()
    try:
        identifier = CallId(call_id)
    except InvariantError:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Not found.") from None
    async with unit_of_work(factory) as session:
        outline = await SqlCallTimelineRepository(session).outline(identifier)
    if outline is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Not found.")
    standing = next((each for each in _standings(request) if each.call_id == identifier), None)
    return CallTimeline(
        call_id=outline.call_id.value,
        state=outline.state.value,
        handling=None if outline.handling is None else outline.handling.value,
        started_at=outline.started_at,
        ended_at=outline.ended_at,
        escalated_at=outline.escalated_at,
        outcome=None if outline.outcome is None else outline.outcome.value,
        participants=[
            ParticipantView(role=each.role.value, joined_at=each.joined_at, left_at=each.left_at)
            for each in outline.participants
        ],
        escalation=(
            None
            if outline.escalation is None
            else EscalationView(
                reason=outline.escalation.reason.value,
                status=outline.escalation.status.value,
                delivery=outline.escalation.delivery.value,
            )
        ),
        marks=[
            TimelineMarkView(at=mark.at, kind=mark.kind.value, name=mark.name)
            for mark in outline.marks
        ],
        live=None if standing is None else _live(request, standing),
    )


@router.get("/metrics", response_model=MetricsView)
async def metrics(request: Request) -> MetricsView:
    """Counts and latency percentiles since this process started, and every circuit's state."""
    observability: Observability = request.app.state.observability
    snapshot = observability.in_process.snapshot()
    return MetricsView(
        counts=[
            CountView(metric=each.metric, labels=dict(each.labels), count=each.count)
            for each in snapshot.counts
        ],
        measures=[
            MeasureView(
                metric=each.metric,
                labels=dict(each.labels),
                count=each.count,
                p50=each.p50,
                p90=each.p90,
                p99=each.p99,
                maximum=each.maximum,
            )
            for each in snapshot.measures
        ],
        dependencies={name: state.value for name, state in observability.circuits.states().items()},
    )


def _standings(request: Request) -> tuple[CallStanding, ...]:
    orchestrator = request.app.state.orchestrator
    return () if orchestrator is None else orchestrator.standings()


def _live(request: Request, standing: CallStanding) -> LiveCall:
    now = container_of(request).clock.now()
    return LiveCall(
        call_id=standing.call_id.value,
        state=standing.state.value,
        since=standing.since,
        seconds_in_state=max((now - standing.since).total_seconds(), 0.0),
    )
