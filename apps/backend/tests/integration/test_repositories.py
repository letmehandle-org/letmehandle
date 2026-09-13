"""The repositories, against a real database.

Everything asserted here behaves differently in a substitute: unique constraints, cascading
deletes, the row count a bulk update reports, and timestamps that keep their timezone. Those are
exactly the behaviours the sign-in flow relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from letmehandle.adapters.database.models import DeviceRow, RefreshTokenRow, UserRow
from letmehandle.adapters.database.repositories import (
    SqlDeviceRepository,
    SqlOTPChallengeRepository,
    SqlRefreshTokenRepository,
    SqlUserRepository,
)
from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.notification import DevicePlatform, DeviceToken
from tests.contracts.fakes import FixedClock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
NUMBER = PhoneNumber.parse("+12025550143")
ANOTHER = PhoneNumber.parse("+12025550144")


def a_user(identifier: str = "user-1", number: PhoneNumber = NUMBER) -> User:
    return User(id=UserId(identifier), phone_number=number)


class TestUsers:
    async def test_a_user_round_trips(self, session: AsyncSession) -> None:
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user())

        found = await users.get(UserId("user-1"))
        assert found is not None
        assert found.phone_number == NUMBER

    async def test_a_user_is_found_by_number(self, session: AsyncSession) -> None:
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user())
        assert await users.find_by_number(NUMBER) is not None

    async def test_an_unknown_user_is_nothing_rather_than_an_error(
        self, session: AsyncSession
    ) -> None:
        users = SqlUserRepository(session, FixedClock(NOW))
        assert await users.get(UserId("nobody")) is None
        assert await users.find_by_number(NUMBER) is None

    async def test_one_number_cannot_have_two_accounts(self, session: AsyncSession) -> None:
        # Enforced by the database, not by a check that a race can slip between. Two requests
        # arriving together for a number with no account is the ordinary case, not a rare one.
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user("user-1"))
        with pytest.raises(IntegrityError):
            await users.add(a_user("user-2"))

    async def test_updates_are_stored(self, session: AsyncSession) -> None:
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user())

        await users.update(
            User(
                id=UserId("user-1"),
                phone_number=NUMBER,
                display_name="Alex",
                preferences=UserPreferences(locale="en-GB"),
            )
        )

        found = await users.get(UserId("user-1"))
        assert found is not None
        assert found.display_name == "Alex"
        assert found.preferences.locale == "en-GB"

    async def test_a_stored_timestamp_keeps_its_timezone(self, session: AsyncSession) -> None:
        # A naive column means whatever the server was set to when the row was written, and a
        # service moved between regions cannot tell what that was.
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge())
        stored = await challenges.get("challenge-1")
        assert stored is not None
        assert stored.issued_at.tzinfo is not None
        assert stored.issued_at == NOW


def a_challenge(identifier: str = "challenge-1", **overrides: object) -> OTPChallenge:
    fields: dict[str, object] = {
        "id": identifier,
        "phone_number": NUMBER,
        "code_hash": "a-hash",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return OTPChallenge(**fields)  # type: ignore[arg-type]


class TestChallenges:
    async def test_a_challenge_round_trips(self, session: AsyncSession) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge())

        stored = await challenges.get("challenge-1")
        assert stored is not None
        assert stored.attempts == 0
        assert stored.verified_at is None

    async def test_a_challenge_whose_code_the_provider_holds_round_trips(
        self, session: AsyncSession
    ) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge(code_hash=None))

        stored = await challenges.get("challenge-1")
        assert stored is not None
        assert stored.code_is_held_by_provider

    async def test_attempts_and_verification_are_stored(self, session: AsyncSession) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge())

        await challenges.update(a_challenge().with_failed_attempt())
        stored = await challenges.get("challenge-1")
        assert stored is not None
        assert stored.attempts == 1

        await challenges.update(stored.verified(NOW))
        verified = await challenges.get("challenge-1")
        assert verified is not None
        assert verified.verified_at == NOW

    async def test_counting_is_per_number_and_within_the_window(
        self, session: AsyncSession
    ) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge("one"))
        await challenges.add(a_challenge("two"))
        await challenges.add(a_challenge("other-number", phone_number=ANOTHER))
        await challenges.add(
            a_challenge(
                "old",
                issued_at=NOW - timedelta(days=1),
                expires_at=NOW - timedelta(days=1) + timedelta(minutes=5),
            )
        )

        assert await challenges.issued_since(NUMBER, NOW - timedelta(hours=1)) == [NOW, NOW]
        assert await challenges.issued_since(ANOTHER, NOW - timedelta(hours=1)) == [NOW]
        assert len(await challenges.issued_since(NUMBER, NOW - timedelta(days=2))) == 3

    async def test_wrong_codes_are_counted_across_challenges_and_the_right_one_is_not(
        self, session: AsyncSession
    ) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge("guessed", attempts=3))
        await challenges.add(a_challenge("then-right", attempts=2, verified_at=NOW))
        await challenges.add(a_challenge("someone-else", phone_number=ANOTHER, attempts=4))

        assert await challenges.failed_attempts_since(NUMBER, NOW - timedelta(hours=1)) == 4
        assert await challenges.failed_attempts_since(ANOTHER, NOW - timedelta(hours=1)) == 4
        assert await challenges.failed_attempts_since(NUMBER, NOW + timedelta(seconds=1)) == 0

    async def test_open_challenges_are_superseded_and_finished_ones_are_left(
        self, session: AsyncSession
    ) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge("open"))
        await challenges.add(a_challenge("used", verified_at=NOW))
        await challenges.add(
            a_challenge("expired", issued_at=NOW - timedelta(hours=1), expires_at=NOW)
        )
        await challenges.add(a_challenge("theirs", phone_number=ANOTHER))

        closed = await challenges.supersede_open(NUMBER, NOW)

        assert closed == 1
        superseded = await challenges.get("open")
        assert superseded is not None
        assert superseded.superseded_at == NOW
        assert not superseded.is_open_at(NOW)
        theirs = await challenges.get("theirs")
        assert theirs is not None
        assert theirs.is_open_at(NOW)

    async def test_the_deployment_counts_every_send_and_each_calling_code(
        self, session: AsyncSession
    ) -> None:
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge("us"))
        await challenges.add(a_challenge("uk", phone_number=PhoneNumber.parse("+447700900123")))
        await challenges.add(
            a_challenge(
                "yesterday",
                issued_at=NOW - timedelta(days=1),
                expires_at=NOW - timedelta(days=1) + timedelta(minutes=5),
            )
        )

        an_hour_ago = NOW - timedelta(hours=1)
        assert await challenges.count_all_issued_since(an_hour_ago) == 2
        assert await challenges.count_all_issued_since(an_hour_ago, "44") == 1
        assert await challenges.count_all_issued_since(an_hour_ago, "1") == 1
        assert await challenges.count_all_issued_since(an_hour_ago, "91") == 0

    async def test_expired_challenges_are_removed(self, session: AsyncSession) -> None:
        # A challenge past its expiry can never succeed, and keeping it is keeping a hash of a
        # credential for no reason.
        challenges = SqlOTPChallengeRepository(session)
        await challenges.add(a_challenge("fresh"))
        await challenges.add(
            a_challenge(
                "stale",
                issued_at=NOW - timedelta(days=1),
                expires_at=NOW - timedelta(days=1) + timedelta(minutes=5),
            )
        )

        removed = await challenges.delete_expired(NOW)

        assert removed == 1
        assert await challenges.get("fresh") is not None
        assert await challenges.get("stale") is None


def a_token(
    identifier: str = "token-1", family: str = "family-1", **overrides: object
) -> RefreshToken:
    fields: dict[str, object] = {
        "id": identifier,
        "family_id": family,
        "user_id": UserId("user-1"),
        "token_hash": f"hash-{identifier}",
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=30),
    }
    fields.update(overrides)
    return RefreshToken(**fields)  # type: ignore[arg-type]


class TestRefreshTokens:
    async def test_a_token_round_trips_and_is_found_by_its_hash(
        self, session: AsyncSession
    ) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token())

        found = await tokens.find_by_hash("hash-token-1")
        assert found is not None
        assert found.family_id == "family-1"

    async def test_an_unknown_hash_finds_nothing(self, session: AsyncSession) -> None:
        assert await SqlRefreshTokenRepository(session).find_by_hash("no-such-hash") is None

    async def test_rotation_is_stored(self, session: AsyncSession) -> None:
        # The refresh path: a token is rotated rather than deleted, so that presenting it again
        # is recognisable as reuse rather than as an unknown token.
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token())

        stored = await tokens.find_by_hash("hash-token-1")
        assert stored is not None
        await tokens.update(stored.rotated(NOW))

        again = await tokens.find_by_hash("hash-token-1")
        assert again is not None
        assert again.was_already_used
        assert again.rotated_at == NOW

    async def test_two_tokens_cannot_share_a_hash(self, session: AsyncSession) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token("one"))
        with pytest.raises(IntegrityError):
            # S106 reads the argument name as a password; it is a column of hashes.
            await tokens.add(a_token("two", token_hash="hash-one"))

    async def test_revoking_a_family_revokes_all_of_it_and_nothing_else(
        self, session: AsyncSession
    ) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token("one", "family-a"))
        await tokens.add(a_token("two", "family-a"))
        await tokens.add(a_token("three", "family-b"))

        revoked = await tokens.revoke_family("family-a", NOW)

        assert revoked == 2
        untouched = await tokens.find_by_hash("hash-three")
        assert untouched is not None
        assert untouched.revoked_at is None

    async def test_revoking_a_family_twice_reports_nothing_the_second_time(
        self, session: AsyncSession
    ) -> None:
        # The count is used to report how many sessions ended. Counting already-revoked rows
        # again would report sessions that were not there to end.
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token("one", "family-a"))

        assert await tokens.revoke_family("family-a", NOW) == 1
        assert await tokens.revoke_family("family-a", NOW) == 0

    async def test_signing_out_everywhere_covers_every_family(self, session: AsyncSession) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token("one", "family-a"))
        await tokens.add(a_token("two", "family-b"))

        assert await tokens.revoke_all_for_user(UserId("user-1"), NOW) == 2

    async def test_deleting_a_user_takes_their_tokens_with_them(
        self, session: AsyncSession
    ) -> None:
        # The cascade is what makes account deletion complete rather than a promise. Phase 12
        # relies on it.
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        await SqlRefreshTokenRepository(session).add(a_token())

        await session.execute(delete(UserRow).where(UserRow.id == "user-1"))
        await session.flush()

        remaining = await session.execute(select(RefreshTokenRow))
        assert remaining.scalars().all() == []


class TestDevices:
    async def test_a_device_round_trips(self, session: AsyncSession) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        devices = SqlDeviceRepository(session, FixedClock(NOW))
        token = DeviceToken(DevicePlatform.IOS, "a-token")

        await devices.register(UserId("user-1"), token)

        assert await devices.tokens_for(UserId("user-1")) == [token]

    async def test_a_token_belongs_to_one_account_at_a_time(self, session: AsyncSession) -> None:
        # A handset changes hands. Two accounts sharing a token would deliver one person's
        # call context to the other's phone.
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user("user-1", NUMBER))
        await users.add(a_user("user-2", ANOTHER))

        devices = SqlDeviceRepository(session, FixedClock(NOW))
        token = DeviceToken(DevicePlatform.IOS, "a-token")
        await devices.register(UserId("user-1"), token)
        await devices.register(UserId("user-2"), token)

        assert await devices.tokens_for(UserId("user-1")) == []
        assert await devices.tokens_for(UserId("user-2")) == [token]

    async def test_a_device_can_be_removed(self, session: AsyncSession) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        devices = SqlDeviceRepository(session, FixedClock(NOW))
        token = DeviceToken(DevicePlatform.ANDROID, "a-token")
        await devices.register(UserId("user-1"), token)

        await devices.remove(UserId("user-1"), token)

        assert await devices.tokens_for(UserId("user-1")) == []

    async def test_removing_a_device_that_is_not_there_is_silent(
        self, session: AsyncSession
    ) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        devices = SqlDeviceRepository(session, FixedClock(NOW))
        await devices.remove(UserId("user-1"), DeviceToken(DevicePlatform.IOS, "absent"))


class TestIsolation:
    async def test_one_user_cannot_read_another_s_devices(self, session: AsyncSession) -> None:
        # The helper every later phase reuses: every resource added from here on gets this
        # proof, because the repository interface is the only thing standing between two
        # people's data.
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user("user-1", NUMBER))
        await users.add(a_user("user-2", ANOTHER))

        devices = SqlDeviceRepository(session, FixedClock(NOW))
        await devices.register(UserId("user-1"), DeviceToken(DevicePlatform.IOS, "theirs"))

        assert await devices.tokens_for(UserId("user-2")) == []

    async def test_one_user_cannot_revoke_another_s_tokens(self, session: AsyncSession) -> None:
        users = SqlUserRepository(session, FixedClock(NOW))
        await users.add(a_user("user-1", NUMBER))
        await users.add(a_user("user-2", ANOTHER))

        tokens = SqlRefreshTokenRepository(session)
        await tokens.add(a_token("theirs", "family-a"))

        assert await tokens.revoke_all_for_user(UserId("user-2"), NOW) == 0
        still_there = await tokens.find_by_hash("hash-theirs")
        assert still_there is not None
        assert still_there.revoked_at is None


class TestDeviceRegistrationIsIdempotent:
    async def test_registering_the_same_token_twice_keeps_one_row_and_refreshes_it(
        self, session: AsyncSession
    ) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        clock = FixedClock(NOW)
        devices = SqlDeviceRepository(session, clock)
        token = DeviceToken(DevicePlatform.IOS, "a-token")

        await devices.register(UserId("user-1"), token)
        clock.advance(60)
        await devices.register(UserId("user-1"), token)

        assert await devices.tokens_for(UserId("user-1")) == [token]
        session.expunge_all()
        rows = (await session.execute(select(DeviceRow))).scalars().all()
        assert [row.registered_at for row in rows] == [NOW + timedelta(seconds=60)]

    async def test_the_same_value_on_two_platforms_is_two_devices(
        self, session: AsyncSession
    ) -> None:
        await SqlUserRepository(session, FixedClock(NOW)).add(a_user())
        devices = SqlDeviceRepository(session, FixedClock(NOW))
        await devices.register(UserId("user-1"), DeviceToken(DevicePlatform.IOS, "same"))
        await devices.register(UserId("user-1"), DeviceToken(DevicePlatform.ANDROID, "same"))
        assert len(await devices.tokens_for(UserId("user-1"))) == 2
