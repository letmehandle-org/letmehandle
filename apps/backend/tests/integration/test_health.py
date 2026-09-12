"""Liveness and readiness answer different questions, and must keep doing so."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle import __version__
from letmehandle.config.settings import Environment
from letmehandle.main import create_app
from tests.support.config import UNREACHABLE_DATABASE, make_settings


async def test_health_reports_ok_and_version(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_health_does_not_need_a_database(client: AsyncClient) -> None:
    """Liveness must answer during a database outage, or an outage becomes a restart loop."""
    assert (await client.get("/health")).status_code == 200


async def test_readiness_is_degraded_without_a_database(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": False}}


async def test_readiness_is_degraded_when_the_database_is_unreachable() -> None:
    """A configured but unreachable database is degraded, not an unhandled error."""
    app = create_app(make_settings(database_url=UNREACHABLE_DATABASE))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            response = await http.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["database"] is False


async def test_every_response_carries_a_correlation_id(client: AsyncClient) -> None:
    assert (await client.get("/health")).headers["x-correlation-id"]


async def test_an_inbound_correlation_id_is_honoured(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"x-correlation-id": "caller-supplied"})
    assert response.headers["x-correlation-id"] == "caller-supplied"


async def test_an_oversized_correlation_id_is_bounded(client: AsyncClient) -> None:
    """An unbounded header becomes an unbounded log field, so it is truncated on the way in."""
    response = await client.get("/health", headers={"x-correlation-id": "x" * 500})
    assert len(response.headers["x-correlation-id"]) == 128


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
async def test_documentation_is_available_outside_production(
    client: AsyncClient, path: str
) -> None:
    assert (await client.get(path)).status_code == 200


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
async def test_documentation_is_absent_in_production(path: str) -> None:
    app = create_app(make_settings(app_env=Environment.PRODUCTION))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            assert (await http.get(path)).status_code == 404
