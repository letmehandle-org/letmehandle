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
    """Readiness: each dependency's answer, each circuit by role, and how limits are counted."""

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]
    dependencies: dict[str, Literal["closed", "open", "half_open"]]
    rate_limits: Literal["shared", "per_process"]


@router.get("/health", response_model=Health, summary="Liveness")
async def health(request: Request) -> Health:
    """Answer without touching a dependency."""
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
        # No container before startup, so nothing is limited yet.
        rate_limits=(
            "shared"
            if container is not None and container.rate_limiter.is_shared
            else "per_process"
        ),
    )
