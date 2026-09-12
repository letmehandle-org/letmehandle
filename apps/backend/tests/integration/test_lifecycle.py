"""Startup and shutdown must be symmetric on every exit path."""

from __future__ import annotations

import pytest

from letmehandle.main import create_app
from tests.support.config import UNREACHABLE_DATABASE, make_settings


async def test_lifespan_starts_and_stops_without_a_database() -> None:
    app = create_app(make_settings())
    async with app.router.lifespan_context(app):
        assert app.state.engine is None
    assert app.state.engine is None


async def test_the_engine_is_created_and_disposed() -> None:
    """Creating a pool and not disposing of it is invisible until connections run out."""
    app = create_app(make_settings(database_url=UNREACHABLE_DATABASE))
    async with app.router.lifespan_context(app):
        engine = app.state.engine
        assert engine is not None
    assert app.state.engine is None
    assert engine.pool.checkedout() == 0


async def test_the_engine_is_disposed_even_when_the_body_raises() -> None:
    """The failure path is the one that leaks, so it is the one worth asserting."""
    app = create_app(make_settings(database_url=UNREACHABLE_DATABASE))
    with pytest.raises(RuntimeError, match="deliberate"):
        async with app.router.lifespan_context(app):
            assert app.state.engine is not None
            raise RuntimeError("deliberate")
    assert app.state.engine is None
