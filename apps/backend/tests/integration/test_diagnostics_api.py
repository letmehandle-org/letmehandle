"""Diagnostics exist only behind their own token, and answer without a database where they can."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle.adapters.speech.session_support.telemetry import ROUND_TRIP
from letmehandle.application.orchestration.run import PROVIDER_FAILED
from letmehandle.bootstrap import build_observability
from letmehandle.main import create_app
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

TOKEN = "diagnostics-bearer-for-integration-only"
AUTHORISED = {"Authorization": f"Bearer {TOKEN}"}
PATHS = ["/diagnostics/calls", "/diagnostics/calls/CAsim-1", "/diagnostics/metrics"]


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@pytest.fixture
async def diagnosed() -> AsyncIterator[tuple[FastAPI, AsyncClient]]:
    settings = make_settings(diagnostics_token=TOKEN)
    app = create_app(settings, observability=build_observability(settings))
    async with serving(app) as http:
        yield app, http


@pytest.mark.parametrize("path", PATHS)
async def test_without_a_token_configured_there_are_no_diagnostics(path: str) -> None:
    async with serving(create_app(make_settings())) as http:
        response = await http.get(path, headers=AUTHORISED)

    assert response.status_code == 404


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-the-diagnostics-token"}, {"Authorization": "Basic x"}],
    ids=["none", "wrong", "not a bearer"],
)
async def test_without_the_token_nothing_is_answered(
    diagnosed: tuple[FastAPI, AsyncClient], path: str, headers: dict[str, str]
) -> None:
    _, http = diagnosed

    response = await http.get(path, headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"] == "not_authenticated"


async def test_a_process_carrying_no_calls_lists_none(
    diagnosed: tuple[FastAPI, AsyncClient],
) -> None:
    _, http = diagnosed

    response = await http.get("/diagnostics/calls", headers=AUTHORISED)

    assert response.status_code == 200
    assert response.json() == {"calls": []}


async def test_a_calls_timeline_needs_the_database(diagnosed: tuple[FastAPI, AsyncClient]) -> None:
    _, http = diagnosed

    response = await http.get("/diagnostics/calls/CAsim-1", headers=AUTHORISED)

    assert response.status_code == 503
    assert response.json()["error"] == "database_unavailable"


async def test_every_measurement_is_reported_as_counts_percentiles_and_circuits(
    diagnosed: tuple[FastAPI, AsyncClient],
) -> None:
    app, http = diagnosed
    metrics = app.state.observability.metrics
    for seconds in (0.1, 0.2, 0.3, 0.4):
        metrics.observe(ROUND_TRIP, seconds, {"provider": "echo"})
    metrics.increment(PROVIDER_FAILED, {"stage": "dial", "kind": "timeout"})

    response = await http.get("/diagnostics/metrics", headers=AUTHORISED)

    assert response.status_code == 200
    assert response.json() == {
        "counts": [
            {
                "metric": PROVIDER_FAILED,
                "labels": {"kind": "timeout", "stage": "dial"},
                "count": 1,
            }
        ],
        "measures": [
            {
                "metric": ROUND_TRIP,
                "labels": {"provider": "echo"},
                "count": 4,
                "p50": 0.2,
                "p90": 0.4,
                "p99": 0.4,
                "maximum": 0.4,
            }
        ],
        "dependencies": {
            "telephony": "closed",
            "speech": "closed",
            "model": "closed",
            "push_ios": "closed",
            "push_android": "closed",
        },
    }


async def test_the_diagnostics_routes_are_not_in_the_published_schema(
    diagnosed: tuple[FastAPI, AsyncClient],
) -> None:
    app, _ = diagnosed

    assert not [path for path in app.openapi()["paths"] if path.startswith("/diagnostics")]
