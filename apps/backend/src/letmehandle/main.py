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
    """Acquire what the application needs, and release it on every exit path."""
    settings: Settings = app.state.settings
    observability: Observability = app.state.observability
    # Checked here too, for an application built by a factory other than `main`.
    settings.require_telephony_configuration()
    engine = None
    try:
        if settings.database_url is not None:
            engine = create_engine(settings)
            app.state.engine = engine
            app.state.session_factory = create_session_factory(engine)

        # Built at startup, so a misconfiguration stops the process rather than a request.
        app.state.container = build_container(
            settings,
            voices=app.state.voices,
            reported_calls=app.state.reported_calls,
            metrics=observability.metrics,
        )
        telephony: tuple[CallTransportBinding, ...] = app.state.telephony
        if app.state.session_factory is not None and telephony:
            # Only a process carrying calls, with storage and transcript keys, notifies users.
            app.state.escalations = build_escalation_dispatcher(
                app.state.container, app.state.session_factory, observability=observability
            )
            # Started before requests, stopped before transports close; starting ends stale calls.
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
        # Last, and on a thread, so every span above is flushed without blocking the loop.
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
    """The application; each parameter replaces what is otherwise built from the settings."""
    resolved = settings or get_settings()
    configure_logging(resolved)

    # Chosen before routing, since the routes depend on it; the container gets this instance.
    chosen_voices = voices or build_voice_provider(resolved)
    # One instance, shared by the reporting route and a handset transport.
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

    # Validated before uvicorn starts, so bad configuration is one line on stderr.
    try:
        settings = get_settings()
        settings.require_signing_key()
        settings.require_voice_catalogue()
        settings.require_telephony_configuration()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error

    uvicorn.run(
        "letmehandle.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - a container binds every interface by design
        port=8000,
        # structlog owns logging; uvicorn writes no second line per request.
        log_config=None,
        access_log=False,
    )
