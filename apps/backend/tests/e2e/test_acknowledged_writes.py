"""A write the application has answered for is readable at once, even when its commit is slow."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e.harness import USERS_LINE, streaming_system

pytestmark = pytest.mark.integration

# A trigger deferred to commit: the insert is written at once, and the commit itself is slow.
SLOW_COMMIT = (
    "CREATE FUNCTION e2e_slow_commit() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN PERFORM pg_sleep(0.5); RETURN NULL; END $$",
    "CREATE CONSTRAINT TRIGGER e2e_slow_commit AFTER INSERT ON users "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION e2e_slow_commit()",
)
FAST_COMMIT = (
    "DROP TRIGGER IF EXISTS e2e_slow_commit ON users",
    "DROP FUNCTION IF EXISTS e2e_slow_commit()",
)


async def _run(engine: AsyncEngine, statements: tuple[str, ...]) -> None:
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))


async def test_a_signed_in_account_can_be_used_as_soon_as_sign_in_answers(database: str) -> None:
    engine = create_async_engine(database)
    await _run(engine, SLOW_COMMIT)
    try:
        async with streaming_system(database) as system:
            http = system.api.http
            challenge = await http.post("/v1/auth/challenge", json={"phone_number": USERS_LINE})
            otp = system.app.state.container.otp
            verified = await http.post(
                "/v1/auth/verify",
                json={"challenge_id": challenge.json()["challenge_id"], "code": otp.sent[-1][1]},
            )
            assert verified.status_code == 200
            token = verified.json()["access_token"]
            me = await http.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

            assert me.status_code == 200, me.text
    finally:
        await _run(engine, FAST_COMMIT)
        await engine.dispose()
