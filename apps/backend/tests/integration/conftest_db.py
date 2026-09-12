"""A real database for the tests that need one.

These tests run against PostgreSQL rather than a substitute. The reasons are specific: unique
constraints, cascading deletes, row counts from a bulk update and timezone-aware timestamps all
behave differently elsewhere, and those are precisely the behaviours being relied upon.

Each test gets its own transaction, rolled back at the end. Faster than recreating the schema,
and it means one test cannot leave anything behind for the next.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from asyncpg.exceptions import PostgresError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from letmehandle.adapters.database.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

# Everything that means "there is no database here for us": refused, unreachable, wrong
# credentials, or a database that does not exist. Each is a local setup problem rather than a
# defect in the code under test.
UNREACHABLE = (OSError, PostgresError, SQLAlchemyError)

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle",
)


@pytest.fixture(scope="session")
def database_url() -> str:
    return DATABASE_URL


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """A session inside a transaction that is always rolled back.

    When no database is reachable this skips with instructions — a developer working on the
    domain should not be blocked by PostgreSQL not running. In CI it fails instead: a suite
    that silently skips its database tests is a suite that reports green while proving nothing
    about the half of the system that talks to a database.
    """
    engine = create_async_engine(database_url)
    try:
        try:
            async with engine.connect():
                pass
        except UNREACHABLE as error:
            # Not silent, and not unconditional: the reason is in the skip message, and in CI
            # this re-raises. A suite that quietly skips its database tests reports green while
            # proving nothing about the half of the system that talks to a database.
            if os.environ.get("CI"):
                raise
            pytest.skip(
                f"no usable database at {database_url.rsplit('@', 1)[-1]}: "
                f"{type(error).__name__}: {error}. Start one with `make up`, or point "
                f"TEST_DATABASE_URL somewhere else."
            )

        async with engine.begin() as connection:
            # create_all rather than running the migrations: this fixture is testing the
            # repositories, and the migrations have a test of their own. Drift between the two
            # is caught by `alembic check`, which is why that runs as well.
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as open_session:
            yield open_session
            await open_session.rollback()
    finally:
        await engine.dispose()
