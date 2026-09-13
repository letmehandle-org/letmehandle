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
        # Keeps bound values, such as phone numbers and names, out of a failed statement's error.
        hide_parameters=True,
    )


async def check_connection(engine: AsyncEngine) -> None:
    """Raise if the database cannot be reached; a query, so a dropped pooled connection counts."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
