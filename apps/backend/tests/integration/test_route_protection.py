"""Every route the application has, checked for who may call it.

Enumerated from the application rather than listed by hand. A list of routes to check is a list
somebody forgets to extend; walking the application means a route added later is checked the
moment it exists, and fails here unless it is signed-in or its exemption is written down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute, iter_route_contexts

from letmehandle.adapters.voice.builtin import BuiltInVoiceProvider
from letmehandle.api.dependencies import CurrentUser, get_authenticated_user
from letmehandle.bootstrap import build_call_transport, build_reported_calls
from letmehandle.domain.ports.voice import VoiceSample
from letmehandle.main import create_app
from tests.support.config import EXAMPLE_DEFAULT_VOICE, EXAMPLE_VOICES
from tests.support.simulated_twilio import telephony_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

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


def _application() -> FastAPI:
    """The application with every optional route present: telephony, and voice preview."""
    settings = telephony_settings()
    binding = build_call_transport(settings, reported_calls=build_reported_calls())
    voices = BuiltInVoiceProvider(
        EXAMPLE_VOICES,
        default_voice_id=EXAMPLE_DEFAULT_VOICE,
        samples={EXAMPLE_DEFAULT_VOICE: VoiceSample(audio=b"sample", media_type="audio/mpeg")},
    )
    return create_app(settings, voices=voices, telephony=binding)


def _routes(app: FastAPI) -> Iterator[tuple[str, str, APIRoute, bool]]:
    """Each method and full path the application answers, with its route and schema visibility.

    Walked through the framework's own route contexts, which see into included routers and
    carry the path with its prefix, the way the schema generator reads them.
    """
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
        # Out of the schema are the telephony provider's callbacks alone. They carry no session:
        # each is proved by the provider's signature in its handler before anything in it is
        # read, which the transport's own suite covers route by route.
        if in_schema and not _signed_in(route.dependant) and (method, path) not in EXEMPT
    ]


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
