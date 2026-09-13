"""Sessions scoped to one unit of work: committed once at the end, rolled back on any error."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from letmehandle.domain.errors import StorageUnavailableError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the factory the application uses for the life of the process."""
    return async_sessionmaker(
        engine,
        # Keeps objects readable after a commit instead of refreshing on a closed transaction.
        expire_on_commit=False,
        autoflush=False,
    )


@asynccontextmanager
async def unit_of_work(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back otherwise; a lost database raises `StorageUnavailableError`."""
    async with factory() as session:
        try:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()
        except Exception as error:
            if _unreachable(error):
                raise StorageUnavailableError from error
            raise


def _unreachable(error: Exception) -> bool:
    if isinstance(error, OperationalError | InterfaceError | OSError):
        return True
    return isinstance(error, DBAPIError) and error.connection_invalidated
