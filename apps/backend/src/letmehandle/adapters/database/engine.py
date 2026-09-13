"""The database connection, owned by the application's lifespan."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from letmehandle.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the engine. Disposing of it is the caller's responsibility, and the lifespan's."""
    return create_async_engine(
        settings.require_database_url(),
        pool_pre_ping=True,
        echo=False,
        # A failed statement's error otherwise renders every bound value, and those include
        # phone numbers and the caller's name. The error's text is what reaches a log line or an
        # error tracker; the statement and the driver's own message are enough to diagnose it.
        hide_parameters=True,
    )


async def check_connection(engine: AsyncEngine) -> None:
    """Raise if the database cannot be reached.

    Deliberately a query rather than a connection check: a pool can hold a connection that the
    server has since dropped, and readiness that reports on a dead connection is worse than no
    readiness check at all.
    """
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
