"""Liveness and readiness, which answer two different questions."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from letmehandle.adapters.database.engine import check_connection
from letmehandle.bootstrap import Container, Observability
from letmehandle.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


class Health(BaseModel):
    """Liveness: is this process running."""

    status: Literal["ok"] = "ok"
    version: str


class Readiness(BaseModel):
    """Readiness: can this process do its job.

    ``checks`` names each dependency and whether it answered. It carries no configuration —
    not a host, not a user, not a URL — because a readiness endpoint is usually the most
    exposed thing an application has.

    ``dependencies`` is where each provider's circuit stands, by role — telephony, speech, the
    model, each push platform — and never by vendor. An open circuit does not make the process
    unready: every process shares the same providers, so taking this one out of rotation would
    move its calls to another that fails them the same way, while this one still does what the
    degraded path allows. ``rate_limits`` says whether limits are counted across processes or in
    each one alone.
    """

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]
    dependencies: dict[str, Literal["closed", "open", "half_open"]]
    rate_limits: Literal["shared", "per_process"]


@router.get("/health", response_model=Health, summary="Liveness")
async def health(request: Request) -> Health:
    """Answer without touching a dependency.

    An orchestrator restarts a process that fails this. Checking the database here would make
    a database outage restart every healthy process, which turns an outage into a worse one.
    """
    version: str = request.app.state.version
    return Health(version=version)


@router.get("/health/ready", response_model=Readiness, summary="Readiness")
async def readiness(request: Request, response: Response) -> Readiness:
    """Answer whether every dependency this process needs is reachable."""
    engine = getattr(request.app.state, "engine", None)
    database_ok = False
    if engine is not None:
        try:
            await check_connection(engine)
            database_ok = True
        except Exception as error:  # noqa: BLE001 - readiness reports, it does not decide
            logger.warning(
                "readiness_check_failed", check="database", error_type=type(error).__name__
            )

    checks = {"database": database_ok}
    ready = all(checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    observability: Observability = request.app.state.observability
    container: Container | None = request.app.state.container
    return Readiness(
        status="ready" if ready else "degraded",
        checks=checks,
        dependencies={name: state.value for name, state in observability.circuits.states().items()},
        # Before startup there is no limiter yet to ask, and nothing is being limited.
        rate_limits=(
            "shared"
            if container is not None and container.rate_limiter.is_shared
            else "per_process"
        ),
    )
