"""Fixtures for tests that need a running application, shared by every integration suite."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
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
    from letmehandle.domain.models.forwarding import ForwardingNumbers
    from letmehandle.domain.ports.otp import OTPProvider
    from letmehandle.domain.ports.voice import VoiceProvider

NUMBER = "+12025550143"
ANOTHER_NUMBER = "+12025550144"


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client speaking to the application in-process, with its lifespan run."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as http:
            yield http


@dataclass(frozen=True, slots=True)
class Api:
    """The running application, and the mock code provider a test reads codes from."""

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
    forwarding: ForwardingNumbers | None = None,
    otp: OTPProvider | None = None,
    resend_cooldowns: tuple[timedelta, ...] = (timedelta(0),),
) -> AsyncIterator[Api]:
    """The whole application on its own engine against one schema, with optional stand-ins."""
    settings = make_settings(transcript_encryption_keys=TEST_TRANSCRIPT_KEYS)
    app: FastAPI = create_app(settings, voices=voices)
    # The schema the `session` fixture created.
    engine: AsyncEngine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )

    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    container = build_container(
        settings, voices=app.state.voices, reported_calls=app.state.reported_calls
    )
    app.state.container = replace(
        container,
        forwarding=container.forwarding if forwarding is None else forwarding,
        # No resend cooldown by default, so a suite can sign the same number in twice.
        auth_limits=replace(container.auth_limits, resend_cooldowns=resend_cooldowns),
        otp=container.otp if otp is None else otp,
    )

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield Api(client=client, app=app)
    finally:
        await engine.dispose()


@pytest.fixture
async def api(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """The application every integration suite talks to, committing on its own engine."""
    async with running(database_url, schema) as ready:
        yield ready


async def code_for(api: Api, number: str = NUMBER) -> tuple[str, str]:
    """Request a challenge and read the code the mock provider recorded."""
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
