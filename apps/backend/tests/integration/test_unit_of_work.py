"""One session per unit of work, committed once or not at all."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle.adapters.database.models import Base, UserRow
from letmehandle.adapters.database.repositories import SqlUserRepository
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.domain.errors import StorageUnavailableError
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User
from tests.contracts.fakes import FixedClock
from tests.support.config import UNREACHABLE_DATABASE

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")


@pytest.fixture
async def engine(session: object, database_url: str, schema: str) -> AsyncIterator[AsyncEngine]:
    """A schema of its own, depending on `session` for its skip and its created schema."""
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
    # A failing handler commits nothing, so no half-written sign-in remains.
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
    # Attributes stay readable after commit (expire_on_commit=False).
    factory = create_session_factory(engine)

    async with unit_of_work(factory) as work:
        users = SqlUserRepository(work, FixedClock(NOW))
        await users.add(User(id=UserId("readable"), phone_number=NUMBER))
        written = await users.get(UserId("readable"))

    assert written is not None
    assert written.phone_number == NUMBER


async def test_a_database_that_cannot_be_reached_is_storage_unavailable() -> None:
    unreachable = create_async_engine(UNREACHABLE_DATABASE)
    try:
        with pytest.raises(StorageUnavailableError) as raised:
            async with unit_of_work(create_session_factory(unreachable)) as work:
                await work.execute(text("SELECT 1"))
    finally:
        await unreachable.dispose()

    assert isinstance(raised.value.__cause__, OSError)


async def test_a_connection_dropped_under_a_unit_of_work_is_storage_unavailable(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(StorageUnavailableError):
        async with unit_of_work(create_session_factory(engine)) as work:
            await work.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))


async def test_a_statement_the_database_refuses_is_not_mistaken_for_an_outage(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(ProgrammingError):
        async with unit_of_work(create_session_factory(engine)) as work:
            await work.execute(text("SELECT * FROM no_such_table"))


async def test_a_failure_that_is_not_the_database_passes_through_unchanged(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(LookupError):
        async with unit_of_work(create_session_factory(engine)):
            raise LookupError
