"""Fixtures for tests that need a running application."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle.main import create_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.config.settings import Settings


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client speaking to the application in-process, with its lifespan run.

    Running the lifespan matters: a test that skips it exercises an application production
    never builds, and startup is exactly where resource handling goes wrong.
    """
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http
