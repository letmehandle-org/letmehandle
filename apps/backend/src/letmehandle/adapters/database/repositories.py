"""Storage against PostgreSQL; every read of personal data filters by its owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.onboarding import OnboardingProgress
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.domain.ports.notification import DevicePlatform, DeviceToken
from letmehandle.domain.ports.reported_calls import CallReport, CallReportRepository
from letmehandle.domain.ports.repositories import (
    DeviceRepository,
    OnboardingRepository,
    OTPChallengeRepository,
    PreferencesRepository,
    RefreshTokenRepository,
    UserRepository,
)

from .models import (
    CallReportRow,
    DeviceRow,
    OnboardingRow,
    OTPChallengeRow,
    PreferencesRow,
    RefreshTokenRow,
    UserRow,
)
from .preference_mapping import (
    document_to_preferences,
    document_to_progress,
    preferences_to_document,
    progress_to_document,
)
from .statements import affected_rows

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.ports.clock import Clock


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

    async def delete(self, user_id: UserId) -> None:
        # One statement: the schema's cascades remove everything that references the user.
        await self._session.execute(delete(UserRow).where(UserRow.id == user_id.value))

    def _to_user(self, row: UserRow) -> User:
        # Only the locale is stored; the rest of `UserPreferences` takes its defaults.
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
                superseded_at=challenge.superseded_at,
            )
        )
        await self._session.flush()

    async def get(self, challenge_id: str) -> OTPChallenge | None:
        # Row lock, so concurrent guesses each count against the attempt limit.
        row = await self._session.get(OTPChallengeRow, challenge_id, with_for_update=True)
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
            superseded_at=row.superseded_at,
        )

    async def update(self, challenge: OTPChallenge) -> None:
        await self._session.execute(
            update(OTPChallengeRow)
            .where(OTPChallengeRow.id == challenge.id)
            .values(
                attempts=challenge.attempts,
                verified_at=challenge.verified_at,
                superseded_at=challenge.superseded_at,
            )
        )
        await self._session.flush()

    async def issued_since(self, number: PhoneNumber, since: datetime) -> list[datetime]:
        result = await self._session.execute(
            select(OTPChallengeRow.issued_at)
            .where(
                OTPChallengeRow.phone_number == number.value,
                OTPChallengeRow.issued_at >= since,
            )
            .order_by(OTPChallengeRow.issued_at)
        )
        return list(result.scalars())

    async def failed_attempts_since(self, number: PhoneNumber, since: datetime) -> int:
        # A verified challenge's last attempt was the right code, so it is not a failure.
        failed = OTPChallengeRow.attempts - case(
            (OTPChallengeRow.verified_at.is_not(None), 1), else_=0
        )
        result = await self._session.execute(
            select(func.coalesce(func.sum(failed), 0)).where(
                OTPChallengeRow.phone_number == number.value,
                OTPChallengeRow.issued_at >= since,
            )
        )
        return int(result.scalar_one())

    async def supersede_open(self, number: PhoneNumber, instant: datetime) -> int:
        result = await self._session.execute(
            update(OTPChallengeRow)
            .where(
                OTPChallengeRow.phone_number == number.value,
                OTPChallengeRow.verified_at.is_(None),
                OTPChallengeRow.superseded_at.is_(None),
                OTPChallengeRow.expires_at > instant,
            )
            .values(superseded_at=instant)
        )
        return affected_rows(result)

    async def count_all_issued_since(self, since: datetime, calling_code: str | None = None) -> int:
        query = (
            select(func.count())
            .select_from(OTPChallengeRow)
            .where(OTPChallengeRow.issued_at >= since)
        )
        if calling_code is not None:
            # A prefix on E.164 is exactly the calling code: codes are prefix-free by design.
            query = query.where(OTPChallengeRow.phone_number.startswith(f"+{calling_code}"))
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def delete_expired(self, before: datetime) -> int:
        result = await self._session.execute(
            delete(OTPChallengeRow).where(OTPChallengeRow.expires_at < before)
        )
        return affected_rows(result)

    async def delete_for_number(self, number: PhoneNumber) -> None:
        await self._session.execute(
            delete(OTPChallengeRow).where(OTPChallengeRow.phone_number == number.value)
        )


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
        # Row lock, so two concurrent exchanges of one token cannot both succeed.
        result = await self._session.execute(
            select(RefreshTokenRow)
            .where(RefreshTokenRow.token_hash == token_hash)
            .with_for_update()
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
        # One statement, so a detected reuse closes the whole family at once.
        result = await self._session.execute(
            update(RefreshTokenRow)
            .where(
                RefreshTokenRow.family_id == family_id,
                RefreshTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=at_instant)
        )
        await self._session.flush()
        return affected_rows(result)

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
        return affected_rows(result)


class SqlPreferencesRepository(PreferencesRepository):
    """Preferences, stored as one document per user."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        if for_update:
            # Locks the user row too, since a first save has no preferences row to lock.
            await self._session.execute(
                select(UserRow.id).where(UserRow.id == user_id.value).with_for_update()
            )
        row = await self._session.get(PreferencesRow, user_id.value, with_for_update=for_update)
        if row is None:
            return None
        return document_to_preferences(row.document)

    async def save(self, user_id: UserId, preferences: UserPreferences) -> None:
        """Insert or replace the whole document in one statement, so saves never race."""
        document = preferences_to_document(preferences)
        now = self._clock.now()
        statement = insert(PreferencesRow).values(
            user_id=user_id.value,
            version=preferences.version,
            document=document,
            updated_at=now,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[PreferencesRow.user_id],
                set_={"version": preferences.version, "document": document, "updated_at": now},
            )
        )
        await self._session.flush()


class SqlOnboardingRepository(OnboardingRepository):
    """How far through setting up each user is."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_id: UserId) -> OnboardingProgress:
        row = await self._session.get(OnboardingRow, user_id.value)
        if row is None:
            # Never started returns empty progress rather than nothing.
            return OnboardingProgress()
        return document_to_progress(row.completed, row.skipped)

    async def save(self, user_id: UserId, progress: OnboardingProgress) -> None:
        document = progress_to_document(progress)
        now = self._clock.now()
        statement = insert(OnboardingRow).values(
            user_id=user_id.value,
            completed=document["completed"],
            skipped=document["skipped"],
            updated_at=now,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[OnboardingRow.user_id],
                set_={
                    "completed": document["completed"],
                    "skipped": document["skipped"],
                    "updated_at": now,
                },
            )
        )
        await self._session.flush()


class SqlDeviceRepository(DeviceRepository):
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        # One upsert moves the token to this account; concurrent registrations do not collide.
        now = self._clock.now()
        statement = insert(DeviceRow).values(
            user_id=user_id.value,
            platform=token.platform.value,
            token=token.value,
            registered_at=now,
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                constraint="uq_user_devices_platform_token",
                set_={"user_id": user_id.value, "registered_at": now},
            )
        )

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


class SqlCallReportRepository(CallReportRepository):
    """What each user's handset has reported, one row per event."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def record(self, user_id: UserId, report: CallReport) -> bool:
        # Insert or do nothing, so a redelivered report counts once.
        result = await self._session.execute(
            insert(CallReportRow)
            .values(
                user_id=user_id.value,
                event_id=report.event_id.value,
                call_id=report.call_id.value,
                kind=report.kind.value,
                screening=None if report.screening is None else report.screening.value,
                ending=None if report.ending is None else report.ending.value,
                occurred_at=report.occurred_at,
                received_at=self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[CallReportRow.user_id, CallReportRow.event_id])
        )
        await self._session.flush()
        return affected_rows(result) == 1

    async def has_ended(self, user_id: UserId, call_id: CallId) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(CallReportRow)
            .where(
                CallReportRow.user_id == user_id.value,
                CallReportRow.call_id == call_id.value,
                CallReportRow.kind == CallEventKind.ENDED.value,
            )
        )
        return int(result.scalar_one()) > 0
