"""One session per unit of work, committed once or not at all."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle.adapters.database.models import Base, UserRow
from letmehandle.adapters.database.repositories import SqlUserRepository
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User
from tests.contracts.fakes import FixedClock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")


@pytest.fixture
async def engine(session: object, database_url: str, schema: str) -> AsyncIterator[AsyncEngine]:
    """A schema of its own.

    Depends on `session` only to inherit its skip when no database is reachable, and because
    it has already created the schema.
    """
    built = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with built.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield built
    finally:
        async with built.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await built.dispose()


async def test_work_that_succeeds_is_committed(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine)

    async with unit_of_work(factory) as work:
        await SqlUserRepository(work, FixedClock(NOW)).add(
            User(id=UserId("committed"), phone_number=NUMBER)
        )

    async with factory() as checking:
        found = await checking.execute(select(UserRow).where(UserRow.id == "committed"))
        assert found.scalar_one_or_none() is not None


async def test_work_that_raises_leaves_nothing_behind(engine: AsyncEngine) -> None:
    # The property that makes a handler atomic without every handler remembering to be. A
    # half-written sign-in is worse than a failed one: the user gets an error and an account
    # they cannot use.
    factory = create_session_factory(engine)

    with pytest.raises(RuntimeError, match="deliberate"):
        async with unit_of_work(factory) as work:
            await SqlUserRepository(work, FixedClock(NOW)).add(
                User(id=UserId("rolled-back"), phone_number=NUMBER)
            )
            raise RuntimeError("deliberate")

    async with factory() as checking:
        found = await checking.execute(select(UserRow).where(UserRow.id == "rolled-back"))
        assert found.scalar_one_or_none() is None


async def test_objects_stay_readable_after_the_commit(engine: AsyncEngine) -> None:
    # Without expire_on_commit=False, reading an attribute of something just written triggers a
    # refresh against a closed transaction — which surfaces in the response serialiser, a long
    # way from the cause.
    factory = create_session_factory(engine)

    async with unit_of_work(factory) as work:
        users = SqlUserRepository(work, FixedClock(NOW))
        await users.add(User(id=UserId("readable"), phone_number=NUMBER))
        written = await users.get(UserId("readable"))

    assert written is not None
    assert written.phone_number == NUMBER
