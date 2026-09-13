"""Fixtures for tests that need a running application.

The `api` fixture here is the one every integration suite uses. It was written inside the
sign-in tests first and moved when a second suite needed it — a second copy would have drifted,
and two suites disagreeing about how the application is assembled is worse than either.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI  # noqa: TC002 - the dataclass evaluates its annotations
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle.adapters.database.session import create_session_factory
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.bootstrap import build_container
from letmehandle.main import create_app
from tests.support.config import TEST_TRANSCRIPT_KEYS, make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from letmehandle.config.settings import Settings
    from letmehandle.domain.models.forwarding import CallForwarding
    from letmehandle.domain.ports.voice import VoiceProvider

NUMBER = "+12025550143"
ANOTHER_NUMBER = "+12025550144"


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


@dataclass(frozen=True, slots=True)
class Api:
    """The running application, and the pieces a test needs to reach around it.

    `otp` is the mock provider, read the way a person reads the text message they were sent. It
    is the only substitute in these tests, and having it here rather than reaching into private
    attributes keeps that honest and visible.
    """

    client: AsyncClient
    app: FastAPI

    @property
    def otp(self) -> MockOTPProvider:
        provider = self.app.state.container.otp
        assert isinstance(provider, MockOTPProvider)
        return provider


@asynccontextmanager
async def running(
    database_url: str,
    schema: str,
    *,
    voices: VoiceProvider | None = None,
    forwarding: CallForwarding | None = None,
) -> AsyncIterator[Api]:
    """The whole application, on its own engine, against one schema.

    Separate from the fixture so that a test needing a differently configured application — a
    voice provider with other capabilities, say — assembles it the same way rather than by
    building a second, subtly different one of its own. `forwarding` stands in for the number a
    streaming deployment's bootstrap chooses, without building that transport's provider.
    """
    # With transcript keys, as a deployment that serves call history has.
    settings = make_settings(transcript_encryption_keys=TEST_TRANSCRIPT_KEYS)
    app: FastAPI = create_app(settings, voices=voices)
    # Pointed at the same schema the `session` fixture created, so the application under test
    # and the fixtures that set it up are looking at the same tables.
    engine: AsyncEngine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )

    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.container = replace(
        build_container(settings, voices=app.state.voices, reported_calls=app.state.reported_calls),
        forwarding=forwarding,
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Api(client=client, app=app)
    finally:
        await engine.dispose()


@pytest.fixture
async def api(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """The application every integration suite talks to.

    Depends on `session` for the schema and for its skip when no database is reachable, then
    runs the application on its own engine so that requests commit for real — which is the
    point: a sign-in that is rolled back proves nothing about a sign-in.
    """
    async with running(database_url, schema) as ready:
        yield ready


async def code_for(api: Api, number: str = NUMBER) -> tuple[str, str]:
    """Request a challenge and read the code the mock recorded.

    Reading it is the equivalent of reading the text message, and it is the only substitute
    anywhere in these tests.
    """
    response = await api.client.post("/v1/auth/challenge", json={"phone_number": number})
    assert response.status_code == 202, response.text
    return response.json()["challenge_id"], api.otp.sent[-1][1]


async def sign_in(api: Api, number: str = NUMBER) -> dict[str, Any]:
    challenge_id, code = await code_for(api, number)
    response = await api.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": code}
    )
    assert response.status_code == 200, response.text
    tokens: dict[str, Any] = response.json()
    return tokens


def bearer(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
