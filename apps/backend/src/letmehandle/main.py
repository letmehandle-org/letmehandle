"""The application, assembled."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from letmehandle import __version__
from letmehandle.adapters.database.engine import create_engine
from letmehandle.api.errors import register_error_handlers
from letmehandle.api.health import router as health_router
from letmehandle.api.middleware import CorrelationMiddleware
from letmehandle.config.settings import ConfigurationError, Settings, get_settings
from letmehandle.observability.logging import configure_logging, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
    engine = None
    try:
        if settings.database_url is not None:
            engine = create_engine(settings)
            app.state.engine = engine
        logger.info("startup", environment=settings.app_env.value, version=__version__)
        yield
    finally:
        if engine is not None:
            await engine.dispose()
            app.state.engine = None
        logger.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Settings are a parameter so that a test can build an app with a configuration of its own
    without reaching into a global. Production passes nothing and gets the validated
    environment.
    """
    resolved = settings or get_settings()
    configure_logging(resolved)

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

    app.add_middleware(CorrelationMiddleware)
    register_error_handlers(app)
    app.include_router(health_router)
    return app


def main() -> None:
    """The entry point used by the container and by ``uv run letmehandle``."""
    import uvicorn

    # Validate before uvicorn starts, so bad configuration is one clear line on stderr rather
    # than a traceback from inside a worker that has already bound a port.
    try:
        get_settings()
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error

    uvicorn.run(
        "letmehandle.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - a container binds every interface by design
        port=8000,
        log_config=None,
    )
