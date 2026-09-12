"""Liveness and readiness, which answer two different questions."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel

from letmehandle.adapters.database.engine import check_connection
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
    """

    status: Literal["ready", "degraded"]
    checks: dict[str, bool]


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
    return Readiness(status="ready" if ready else "degraded", checks=checks)
