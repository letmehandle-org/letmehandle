"""The application, assembled."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from letmehandle import __version__
from letmehandle.adapters.database.engine import create_engine
from letmehandle.adapters.database.session import create_session_factory
from letmehandle.api.auth import router as auth_router
from letmehandle.api.call_reports import router as call_reports_router
from letmehandle.api.calls import router as calls_router
from letmehandle.api.diagnostics import router as diagnostics_router
from letmehandle.api.errors import register_error_handlers
from letmehandle.api.escalations import router as escalations_router
from letmehandle.api.health import router as health_router
from letmehandle.api.middleware import CorrelationMiddleware
from letmehandle.api.preferences import router as preferences_router
from letmehandle.api.voices import build_voice_router
from letmehandle.bootstrap import (
    build_call_orchestrator,
    build_call_transports,
    build_container,
    build_escalation_dispatcher,
    build_observability,
    build_reported_calls,
    build_voice_provider,
    close_providers,
)
from letmehandle.config.settings import ConfigurationError, Settings, get_settings
from letmehandle.observability.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from letmehandle.application.orchestration.ports import AssistantServices
    from letmehandle.bootstrap import CallTransportBinding, Observability
    from letmehandle.domain.ports.voice import VoiceProvider

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Acquire what the application needs, and release it on every exit path.

    The release is in a ``finally`` rather than after the ``yield`` alone, because a failure
    during shutdown elsewhere in the stack would otherwise leak the pool — and a leaked pool is
    invisible until a process has been restarted enough times to exhaust the server's
    connections.
    """
    settings: Settings = app.state.settings
    observability: Observability = app.state.observability
    # Here as well as in `main`: an application built by a factory other than `main` must not
    # take calls it has nothing to orchestrate them with either.
    settings.require_telephony_configuration()
    engine = None
    try:
        if settings.database_url is not None:
            engine = create_engine(settings)
            app.state.engine = engine
            app.state.session_factory = create_session_factory(engine)

        # Built once, at startup, so that a misconfiguration is a process that does not start
        # rather than a request that fails in front of somebody.
        app.state.container = build_container(
            settings, voices=app.state.voices, reported_calls=app.state.reported_calls
        )
        telephony: tuple[CallTransportBinding, ...] = app.state.telephony
        if app.state.session_factory is not None and telephony:
            # What call orchestration asks to notify a user, and nothing else does. It needs
            # storage and the transcript keys, so a process that carries no calls builds none.
            app.state.escalations = build_escalation_dispatcher(
                app.state.container, app.state.session_factory, observability=observability
            )
            # One owner of every call on every line, started before the application takes
            # requests and stopped before the transports are closed beneath it. Starting ends
            # whatever calls a previous process left unfinished.
            orchestrator = build_call_orchestrator(
                settings,
                container=app.state.container,
                session_factory=app.state.session_factory,
                telephony=telephony,
                dispatcher=app.state.escalations,
                observability=observability,
                assistant=app.state.assistant,
            )
            await orchestrator.start()
            app.state.orchestrator = orchestrator

        logger.info(
            "startup",
            environment=settings.app_env.value,
            version=__version__,
            otp_provider=app.state.container.otp.name,
            voice_provider=app.state.voices.name,
            notification_providers=[each.name for each in app.state.container.notifications],
        )
        yield
    finally:
        if app.state.orchestrator is not None:
            await app.state.orchestrator.stop()
            app.state.orchestrator = None
        if app.state.escalations is not None:
            await app.state.escalations.aclose()
            app.state.escalations = None
        if app.state.container is not None:
            await close_providers(app.state.container)
        bindings: tuple[CallTransportBinding, ...] = app.state.telephony
        for binding in bindings:
            await binding.close()
        if engine is not None:
            await engine.dispose()
            app.state.engine = None
            app.state.session_factory = None
        app.state.container = None
        # Last, so the spans of everything stopped above are among those flushed. On a thread: the
        # exporter waits on the network, and the loop still has connections to close.
        await asyncio.to_thread(observability.close)
        logger.info("shutdown")


def create_app(
    settings: Settings | None = None,
    *,
    voices: VoiceProvider | None = None,
    telephony: Sequence[CallTransportBinding] | None = None,
    assistant: AssistantServices | None = None,
    observability: Observability | None = None,
) -> FastAPI:
    """Build the application.

    Settings are a parameter so that a test can build an app with a configuration of its own
    without reaching into a global. Production passes nothing and gets the validated
    environment.

    The voice provider is a parameter for the same reason and one more: which routes exist
    depends on what it can do, and there is no configuration that selects a second provider
    yet — so a test of that behaviour has no other way in.

    The call transports are chosen here for the same reason as the voices: a provider's routes
    exist only when its transport does. A test passes them wired to a simulated provider, and the
    speech service and agent its calls are taken with, which production builds from settings.

    Observability is a parameter for the transport's sake: its routes record and trace through it,
    so a test that builds the transport builds it first and hands the same one on here.
    """
    resolved = settings or get_settings()
    configure_logging(resolved)

    # Chosen here rather than at startup because the routes below are decided from what it
    # can do, and routing is settled before the application ever runs. The container is handed
    # this same instance, so nothing can answer the question twice and differently.
    chosen_voices = voices or build_voice_provider(resolved)
    # One for the life of the application, for the same reason: the container's reporting route
    # and a handset transport chosen below must be the same instance.
    reported_calls = build_reported_calls()
    chosen_observability = observability or build_observability(resolved)
    chosen_telephony = (
        tuple(telephony)
        if telephony is not None
        else build_call_transports(
            resolved, reported_calls=reported_calls, observability=chosen_observability
        )
    )

    app = FastAPI(
        title="LetMeHandle",
        version=__version__,
        summary="An agent that handles phone calls on someone's behalf.",
        lifespan=lifespan,
        docs_url=None if resolved.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.is_production else "/openapi.json",
    )
    app.state.settings = resolved
    app.state.version = __version__
    app.state.engine = None
    app.state.session_factory = None
    app.state.container = None
    app.state.escalations = None
    app.state.orchestrator = None
    app.state.assistant = assistant
    app.state.voices = chosen_voices
    app.state.reported_calls = reported_calls
    app.state.telephony = chosen_telephony
    app.state.observability = chosen_observability

    app.add_middleware(CorrelationMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(preferences_router)
    app.include_router(calls_router)
    app.include_router(escalations_router)
    app.include_router(call_reports_router)
    app.include_router(diagnostics_router)
    app.include_router(build_voice_router(chosen_voices))
    for binding in chosen_telephony:
        app.include_router(binding.router)
    return app


def main() -> None:
    """The entry point used by the container and by ``uv run letmehandle``."""
    import uvicorn

    # Validate before uvicorn starts, so bad configuration is one clear line on stderr rather
    # than a traceback from inside a worker that has already bound a port.
    try:
        # The catalogue as well as the settings: every request for voices needs it, and a service
        # that starts without it fails in front of somebody instead of here.
        settings = get_settings()
        settings.require_voice_catalogue()
        settings.require_telephony_configuration()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error

    uvicorn.run(
        "letmehandle.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - a container binds every interface by design
        port=8000,
        # Logging belongs to structlog. Leaving uvicorn's own dictConfig in place would emit a
        # second, unstructured line for every request, carrying no correlation id — two
        # accounts of the same event, one of them useless.
        log_config=None,
        access_log=False,
    )
