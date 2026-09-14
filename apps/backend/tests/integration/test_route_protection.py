"""Every route the application has, checked for who may call it and how much body it reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts
from httpx import ASGITransport, AsyncClient

from letmehandle.adapters.voice.builtin import BuiltInVoiceProvider
from letmehandle.api.dependencies import CurrentUser, get_authenticated_user
from letmehandle.bootstrap import build_call_transports, build_reported_calls
from letmehandle.domain.ports.voice import VoiceSample
from letmehandle.main import create_app
from tests.support.config import EXAMPLE_DEFAULT_VOICE, EXAMPLE_VOICES, make_settings
from tests.support.observability import recorded_observability
from tests.support.simulated_twilio import telephony_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from fastapi.dependencies.models import Dependant

# Routes that answer somebody who is not signed in, each with the reason it has to.
EXEMPT: Final[dict[tuple[str, str], str]] = {
    ("GET", "/health"): "an orchestrator's liveness probe carries no credentials",
    ("GET", "/health/ready"): "a readiness probe carries no credentials, and says no configuration",
    ("POST", "/v1/auth/challenge"): "signing in starts from having no session",
    ("POST", "/v1/auth/verify"): "exchanging a code is how a session is obtained",
    ("POST", "/v1/auth/refresh"): "the refresh token in the body is the credential",
    ("POST", "/v1/auth/signout"): "the refresh token in the body is the credential",
}

# Larger than any route here reads, and small enough to send in a test.
OVERSIZED_BODY: Final = b"{" + b" " * (2 * 1024 * 1024) + b"}"


def _application() -> FastAPI:
    """The application with every optional route present: telephony, and voice preview."""
    bindings = build_call_transports(
        telephony_settings(),
        reported_calls=build_reported_calls(),
        observability=recorded_observability(),
    )
    voices = BuiltInVoiceProvider(
        EXAMPLE_VOICES,
        default_voice_id=EXAMPLE_DEFAULT_VOICE,
        samples={EXAMPLE_DEFAULT_VOICE: VoiceSample(audio=b"sample", media_type="audio/mpeg")},
    )
    # Handed the transport, so the app starts without the storage a configured transport requires.
    return create_app(make_settings(), voices=voices, telephony=bindings)


def _routes(app: FastAPI) -> Iterator[tuple[str, str, APIRoute, bool]]:
    """Each method and full path the application answers, with its route and schema visibility."""
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute):
            for method in sorted(context.methods or ()):
                yield method, str(context.path), route, bool(context.include_in_schema)


def _signed_in(dependant: Dependant) -> bool:
    """Whether the route's dependencies, at any depth, include verifying an access token."""
    return any(
        dependency.call is get_authenticated_user or _signed_in(dependency)
        for dependency in dependant.dependencies
    )


def unprotected_routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every method and path that neither needs a session nor is exempt."""
    return [
        (method, path)
        for method, path, route, in_schema in _routes(app)
        # Only the provider's callbacks are out of the schema, each verified by its signature.
        if in_schema and not _signed_in(route.dependant) and (method, path) not in EXEMPT
    ]


@pytest.fixture
async def everything() -> AsyncIterator[AsyncClient]:
    app = _application()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        yield http


class TestAuthorisation:
    def test_every_route_needs_a_signed_in_user_or_says_why_not(self) -> None:
        assert unprotected_routes(_application()) == []

    def test_every_exemption_names_a_route_that_exists(self) -> None:
        # An exemption left behind by a removed route is one a new route can quietly reuse.
        present = {(method, path) for method, path, _, _ in _routes(_application())}
        assert set(EXEMPT) <= present

    def test_a_route_without_a_session_is_found(self) -> None:
        app = _application()
        forgotten = APIRouter()

        @forgotten.get("/v1/forgotten")
        async def read_forgotten() -> dict[str, str]:
            return {}

        app.include_router(forgotten)

        assert unprotected_routes(app) == [("GET", "/v1/forgotten")]

    def test_a_route_that_takes_the_signed_in_user_is_not_found(self) -> None:
        app = _application()
        remembered = APIRouter()

        @remembered.get("/v1/remembered")
        async def read_remembered(user: CurrentUser) -> dict[str, str]:
            return {"id": user.id.value}

        app.include_router(remembered)

        assert unprotected_routes(app) == []


class TestBodyLimits:
    async def test_every_route_that_reads_a_body_refuses_an_oversized_one(
        self, everything: AsyncClient
    ) -> None:
        # A body is read before parsing and authentication, so every route accepting one caps it.
        accepting = [
            (method, path)
            for method, path, route, in_schema in _routes(_application())
            if method in {"POST", "PUT", "PATCH"}
            and (route.body_field is not None or not in_schema)
        ]
        assert accepting

        answers = {}
        for method, path in accepting:
            response = await everything.request(
                method,
                path.replace("{call_id}", "a-call").replace("{voice_id}", EXAMPLE_DEFAULT_VOICE),
                content=OVERSIZED_BODY,
                headers={"content-type": "application/json"},
            )
            answers[(method, path)] = response.status_code

        assert {route: status for route, status in answers.items() if status != 413} == {}
