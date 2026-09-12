"""Sessions, scoped to one unit of work.

A session is created per request and committed once, at the end, if nothing raised. That is
what makes a handler atomic without every handler remembering to be: a failure halfway through
leaves the database as it was rather than half-changed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the factory the application uses for the life of the process."""
    return async_sessionmaker(
        engine,
        # Objects stay usable after a commit. Without this, reading an attribute of something
        # just written triggers a refresh against a closed transaction, which surfaces as an
        # error in the response serialiser rather than anywhere near the cause.
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def unit_of_work(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One session, committed on success and rolled back on anything else.

    The rollback is not belt and braces: an exception on the way out of a handler must not
    leave a half-written sign-in behind, and relying on the session's own cleanup leaves that
    to whether the garbage collector gets there first.
    """
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()
