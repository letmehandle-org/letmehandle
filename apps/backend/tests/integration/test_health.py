"""Liveness and readiness answer different questions, and must keep doing so."""

from __future__ import annotations

from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle import __version__
from letmehandle.application.resilience.circuit import CircuitPolicy, Dependency
from letmehandle.bootstrap import build_observability
from letmehandle.config.settings import Environment
from letmehandle.domain.errors import InvariantError
from letmehandle.main import create_app
from tests.contracts.auth_fakes import NeverLimits
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
    assert response.json() == {
        "status": "degraded",
        "checks": {"database": False},
        "dependencies": {
            "telephony": "closed",
            "speech": "closed",
            "model": "closed",
            "push_ios": "closed",
            "push_android": "closed",
        },
        "rate_limits": "per_process",
    }


async def test_readiness_says_a_dependency_is_failing_without_being_taken_out_of_rotation() -> None:
    # Every process shares the providers, so an open circuit here is open everywhere: readiness
    # reports it and stays what the database makes it.
    settings = make_settings()
    observability = build_observability(settings)
    app = create_app(settings, observability=observability)
    breaker = observability.circuits[Dependency.SPEECH]
    for _ in range(CircuitPolicy().failures_to_open):
        with pytest.raises(TimeoutError):
            await breaker.call(_times_out)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            response = await http.get("/health/ready")

    body = response.json()
    assert body["dependencies"]["speech"] == "open"
    assert body["checks"] == {"database": False}
    assert set(body) == {"status", "checks", "dependencies", "rate_limits"}


async def test_readiness_says_when_rate_limits_are_counted_across_processes() -> None:
    app = create_app(make_settings())
    async with app.router.lifespan_context(app):
        app.state.container = replace(app.state.container, rate_limiter=NeverLimits())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            response = await http.get("/health/ready")

    assert response.json()["rate_limits"] == "shared"


async def test_readiness_before_startup_reports_no_shared_limiter() -> None:
    app = create_app(make_settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/health/ready")

    assert response.json()["rate_limits"] == "per_process"


async def _times_out() -> None:
    raise TimeoutError


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


def test_documentation_is_not_served_in_production() -> None:
    # Asserted on the application rather than by making a request, because a production
    # application cannot currently start: the only one-time-password provider is the mock, and
    # it refuses. See test_a_production_configuration_refuses_the_mock_provider.
    app = create_app(make_settings(app_env=Environment.PRODUCTION))
    assert app.docs_url is None
    assert app.openapi_url is None


async def test_a_production_configuration_refuses_the_mock_provider() -> None:
    """The application will not start in production today, and that is the intended state.

    No provider that actually delivers a code exists yet — the first one arrives with the
    telephony work. Until then a production deployment is refused loudly at startup rather than
    running with a provider that would let anybody sign in as anybody.
    """
    app = create_app(make_settings(app_env=Environment.PRODUCTION))
    with pytest.raises(InvariantError, match="cannot run in production"):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - the context never opens
