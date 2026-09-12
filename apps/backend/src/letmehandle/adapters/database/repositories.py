"""Storage, implemented against PostgreSQL.

Every method that reads something belonging to a person filters by that person. The interfaces
make it impossible to ask otherwise; this is where that promise is kept.

Mapping between rows and domain types happens here and only here. A domain object never learns
what a column is called, and a row never carries a rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, delete, func, select, update

from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.notification import DevicePlatform, DeviceToken
from letmehandle.domain.ports.repositories import (
    DeviceRepository,
    OTPChallengeRepository,
    RefreshTokenRepository,
    UserRepository,
)

from .models import DeviceRow, OTPChallengeRow, RefreshTokenRow, UserRow

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.engine import Result
    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.ports.clock import Clock


def _affected(result: Result[Any]) -> int:
    """How many rows a statement changed.

    `Session.execute` is typed as returning a `Result`, which has no row count; a data
    modification actually returns a `CursorResult`, which does. The cast says so once here
    rather than at each of the four call sites, where it would read as a workaround.
    """
    return int(cast("CursorResult[Any]", result).rowcount or 0)


class SqlUserRepository(UserRepository):
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserRow, user_id.value)
        return None if row is None else self._to_user(row)

    async def find_by_number(self, number: PhoneNumber) -> User | None:
        result = await self._session.execute(
            select(UserRow).where(UserRow.phone_number == number.value)
        )
        row = result.scalar_one_or_none()
        return None if row is None else self._to_user(row)

    async def add(self, user: User) -> None:
        now = self._clock.now()
        self._session.add(
            UserRow(
                id=user.id.value,
                phone_number=user.phone_number.value,
                display_name=user.display_name,
                locale=user.preferences.locale,
                created_at=now,
                updated_at=now,
            )
        )
        await self._session.flush()

    async def update(self, user: User) -> None:
        await self._session.execute(
            update(UserRow)
            .where(UserRow.id == user.id.value)
            .values(
                display_name=user.display_name,
                locale=user.preferences.locale,
                updated_at=self._clock.now(),
            )
        )

    def _to_user(self, row: UserRow) -> User:
        # Only the preferences this phase stores. The rest of `UserPreferences` arrives in
        # phase 3 with its own tables; defaulting them here keeps the domain type whole
        # without inventing storage that does not exist yet.
        return User(
            id=UserId(row.id),
            phone_number=PhoneNumber(row.phone_number),
            display_name=row.display_name,
            preferences=UserPreferences(locale=row.locale),
        )


class SqlOTPChallengeRepository(OTPChallengeRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, challenge: OTPChallenge) -> None:
        self._session.add(
            OTPChallengeRow(
                id=challenge.id,
                phone_number=challenge.phone_number.value,
                code_hash=challenge.code_hash,
                issued_at=challenge.issued_at,
                expires_at=challenge.expires_at,
                attempts=challenge.attempts,
                verified_at=challenge.verified_at,
            )
        )
        await self._session.flush()

    async def get(self, challenge_id: str) -> OTPChallenge | None:
        row = await self._session.get(OTPChallengeRow, challenge_id)
        if row is None:
            return None
        return OTPChallenge(
            id=row.id,
            phone_number=PhoneNumber(row.phone_number),
            code_hash=row.code_hash,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            attempts=row.attempts,
            verified_at=row.verified_at,
        )

    async def update(self, challenge: OTPChallenge) -> None:
        await self._session.execute(
            update(OTPChallengeRow)
            .where(OTPChallengeRow.id == challenge.id)
            .values(attempts=challenge.attempts, verified_at=challenge.verified_at)
        )
        await self._session.flush()

    async def count_issued_since(self, number: PhoneNumber, since: datetime) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(OTPChallengeRow)
            .where(
                OTPChallengeRow.phone_number == number.value,
                OTPChallengeRow.issued_at >= since,
            )
        )
        return int(result.scalar_one())

    async def delete_expired(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(OTPChallengeRow).where(OTPChallengeRow.expires_at < before)
        )
        return _affected(result)


class SqlRefreshTokenRepository(RefreshTokenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> None:
        self._session.add(
            RefreshTokenRow(
                id=token.id,
                family_id=token.family_id,
                user_id=token.user_id.value,
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                rotated_at=token.rotated_at,
                revoked_at=token.revoked_at,
            )
        )
        await self._session.flush()

    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self._session.execute(
            select(RefreshTokenRow).where(RefreshTokenRow.token_hash == token_hash)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return RefreshToken(
            id=row.id,
            family_id=row.family_id,
            user_id=UserId(row.user_id),
            token_hash=row.token_hash,
            issued_at=row.issued_at,
            expires_at=row.expires_at,
            rotated_at=row.rotated_at,
            revoked_at=row.revoked_at,
        )

    async def update(self, token: RefreshToken) -> None:
        await self._session.execute(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.id == token.id)
            .values(rotated_at=token.rotated_at, revoked_at=token.revoked_at)
        )
        await self._session.flush()

    async def revoke_family(self, family_id: str, at_instant: datetime) -> int:
        # One statement, because this runs on a path that has just detected a stolen token and
        # the window between detecting and closing is the window an attacker has.
        result = await self._session.execute(
            update(RefreshTokenRow)
            .where(
                RefreshTokenRow.family_id == family_id,
                RefreshTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=at_instant)
        )
        await self._session.flush()
        return _affected(result)

    async def revoke_all_for_user(self, user_id: UserId, at_instant: datetime) -> int:
        result = await self._session.execute(
            update(RefreshTokenRow)
            .where(
                RefreshTokenRow.user_id == user_id.value,
                RefreshTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=at_instant)
        )
        await self._session.flush()
        return _affected(result)


class SqlDeviceRepository(DeviceRepository):
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        # Removed from wherever it was first. A handset changes hands, and two accounts sharing
        # a token would send one person's call context to the other's phone.
        await self._session.execute(
            delete(DeviceRow).where(
                DeviceRow.platform == token.platform.value, DeviceRow.token == token.value
            )
        )
        self._session.add(
            DeviceRow(
                user_id=user_id.value,
                platform=token.platform.value,
                token=token.value,
                registered_at=self._clock.now(),
            )
        )
        await self._session.flush()

    async def tokens_for(self, user_id: UserId) -> list[DeviceToken]:
        result = await self._session.execute(
            select(DeviceRow).where(DeviceRow.user_id == user_id.value)
        )
        return [
            DeviceToken(DevicePlatform(row.platform), row.token) for row in result.scalars().all()
        ]

    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        await self._session.execute(
            delete(DeviceRow).where(
                DeviceRow.user_id == user_id.value,
                DeviceRow.platform == token.platform.value,
                DeviceRow.token == token.value,
            )
        )
        await self._session.flush()
