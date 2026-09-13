"""The readiness check must answer truthfully in both directions."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle.adapters.database.engine import check_connection, create_engine
from letmehandle.main import create_app
from tests.support.config import UNREACHABLE_DATABASE, make_settings

# A real in-process database, because the check issues a query rather than taking a connection.
SQLITE = "sqlite+aiosqlite:///:memory:"


async def test_check_connection_succeeds_against_a_live_database() -> None:
    engine = create_async_engine(SQLITE)
    try:
        await check_connection(engine)
    finally:
        await engine.dispose()


async def test_check_connection_raises_when_the_database_is_unreachable() -> None:
    """A refused connection raises the driver's own OSError, not only SQLAlchemy's errors."""
    engine = create_async_engine(UNREACHABLE_DATABASE)
    try:
        with pytest.raises((SQLAlchemyError, OSError)):
            await check_connection(engine)
    finally:
        await engine.dispose()


def test_create_engine_requires_a_configured_url() -> None:
    engine = create_engine(make_settings(database_url=UNREACHABLE_DATABASE))
    assert engine.url.drivername == "postgresql+asyncpg"


async def test_readiness_is_ready_when_the_database_answers() -> None:
    """The positive path, driven by a database that genuinely responds."""
    app = create_app(make_settings())
    async with app.router.lifespan_context(app):
        engine = create_async_engine(SQLITE)
        app.state.engine = engine
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as http:
                response = await http.get("/health/ready")
        finally:
            await engine.dispose()
            app.state.engine = None

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["checks"]) == ("ready", {"database": True})
