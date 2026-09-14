"""A real PostgreSQL database for the tests that need one, one rolled-back transaction per test."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from asyncpg.exceptions import PostgresError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from letmehandle.adapters.database.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# Every error meaning no usable database is reachable, which is a local setup problem.
UNREACHABLE = (OSError, PostgresError, SQLAlchemyError)

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle",
)


# Each test run gets a schema named after its process, so concurrent runs do not share tables.
SCHEMA = f"test_{os.getpid()}"


@pytest.fixture(scope="session")
def database_url() -> str:
    return DATABASE_URL


@pytest.fixture(scope="session")
def schema() -> str:
    """The schema this run owns. Nothing outside it is touched."""
    return SCHEMA


@pytest.fixture
async def session(database_url: str, schema: str) -> AsyncIterator[AsyncSession]:
    """A session in an always rolled-back transaction; skips without a database, fails in CI."""
    engine = create_async_engine(database_url)
    try:
        try:
            async with engine.connect():
                pass
        except UNREACHABLE as error:
            # Skips with the reason locally and re-raises in CI.
            if os.environ.get("CI"):
                raise
            pytest.skip(
                f"no usable database at {database_url.rsplit('@', 1)[-1]}: "
                f"{type(error).__name__}: {error}. Start one with `make up`, or point "
                f"TEST_DATABASE_URL somewhere else."
            )

        async with engine.begin() as connection:
            # Dropped and recreated so a schema change between runs leaves no stale column behind.
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET search_path TO "{schema}"'))
            # Built from the models; migrations have their own test and `alembic check`.
            await connection.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as open_session:
            # Set per session too, because a pooled connection without it uses `public`.
            await open_session.execute(text(f'SET search_path TO "{schema}"'))
            yield open_session
            await open_session.rollback()
    finally:
        await engine.dispose()
