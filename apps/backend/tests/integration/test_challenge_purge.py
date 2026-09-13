"""Sign-in challenges are forgotten by the scheduled purge once nothing can use them.

A challenge holds the number a code was sent to, and anybody can make one for any number. Kept
forever, the table becomes a list of every number anyone ever typed into the sign-in screen,
whether or not it belongs to an account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from letmehandle.adapters.database.models import OTPChallengeRow
from letmehandle.adapters.database.repositories import SqlOTPChallengeRepository
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.application.auth.service import AuthenticationPolicy
from letmehandle.domain.models.auth import CHALLENGE_LIFETIME, OTPChallenge
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.purge import purge_expired_challenges
from tests.contracts.fakes import FixedClock
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550161")
COUNTING_WINDOW = AuthenticationPolicy().challenges_per_number_window


@pytest.fixture
async def engine(
    session: AsyncSession, database_url: str, schema: str
) -> AsyncIterator[AsyncEngine]:
    """An engine on the test's schema whose transactions commit, as the purge's do."""
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def issued(engine: AsyncEngine, challenge_id: str, at_instant: datetime) -> None:
    async with unit_of_work(create_session_factory(engine)) as session:
        await SqlOTPChallengeRepository(session).add(
            OTPChallenge(
                id=challenge_id,
                phone_number=NUMBER,
                code_hash="scrypt$00$00",
                issued_at=at_instant,
                expires_at=at_instant + CHALLENGE_LIFETIME,
            )
        )


async def remaining(engine: AsyncEngine) -> set[str]:
    async with create_session_factory(engine)() as session:
        return set((await session.execute(select(OTPChallengeRow.id))).scalars())


async def test_a_challenge_outside_the_counting_window_is_deleted(engine: AsyncEngine) -> None:
    await issued(
        engine, "long-gone", NOW - COUNTING_WINDOW - CHALLENGE_LIFETIME - timedelta(minutes=1)
    )
    await issued(engine, "open", NOW)

    deleted = await purge_expired_challenges(make_settings(), engine=engine, clock=FixedClock(NOW))

    assert deleted == 1
    assert await remaining(engine) == {"open"}


async def test_an_expired_challenge_still_counted_against_its_number_is_kept(
    engine: AsyncEngine,
) -> None:
    # The per-number limit counts the challenges issued within its window. Deleting one as soon as
    # it expires would hand a number its limit back five minutes after it was spent.
    await issued(engine, "expired-but-counted", NOW - COUNTING_WINDOW + timedelta(minutes=1))

    deleted = await purge_expired_challenges(make_settings(), engine=engine, clock=FixedClock(NOW))

    assert deleted == 0
    assert await remaining(engine) == {"expired-but-counted"}
