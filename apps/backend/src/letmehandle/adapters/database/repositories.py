"""Storage, implemented against PostgreSQL.

Every method that reads something belonging to a person filters by that person. The interfaces
make it impossible to ask otherwise; this is where that promise is kept.

Mapping between rows and domain types happens here and only here. A domain object never learns
what a column is called, and a row never carries a rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
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
    EscalationContextRepository,
    OnboardingRepository,
    OTPChallengeRepository,
    PreferencesRepository,
    RefreshTokenRepository,
    UserRepository,
)

from .models import (
    CallReportRow,
    DeviceRow,
    EscalationContextRow,
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


class SqlPreferencesRepository(PreferencesRepository):
    """Preferences, stored as one document per user."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        if for_update:
            # The user's row as well as the preferences row: before a user's first save there is
            # no preferences row to lock, and two first saves would each compose from the defaults.
            await self._session.execute(
                select(UserRow.id).where(UserRow.id == user_id.value).with_for_update()
            )
        row = await self._session.get(PreferencesRow, user_id.value, with_for_update=for_update)
        if row is None:
            return None
        return document_to_preferences(row.document)

    async def save(self, user_id: UserId, preferences: UserPreferences) -> None:
        """Insert or replace, in one statement.

        One statement rather than read-then-write: two requests saving different sections at the
        same time would otherwise race, and the loser's change would disappear with nothing to
        show for it. The application composes a whole set before calling this, so replacing is
        the correct operation.
        """
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
            # Never started is a position in the flow, not an absence. Returning empty progress
            # rather than nothing means no caller has to remember to handle the first visit.
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
        # One statement that moves the token to this account if another holds it. A handset
        # changes hands, and two accounts sharing a token would send one person's call context to
        # the other's phone. Not a delete and then an insert: an app registers on every launch,
        # and two launches racing through a delete-then-insert both insert, and one fails on the
        # unique constraint.
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


class SqlEscalationContextRepository(EscalationContextRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, user_id: UserId, context: EscalationContext) -> bool:
        # One statement, so two dispatches racing for the same call cannot both win: the database
        # decides which insert happened, and the other sees nothing returned.
        statement = (
            insert(EscalationContextRow)
            .values(
                user_id=user_id.value,
                call_id=context.call_id.value,
                reason=context.reason.value,
                caller_label=context.caller_label,
                established=context.established,
                needed=context.needed,
                status=context.status.value,
                delivery=context.delivery.value,
                raised_at=context.raised_at,
                ended_at=context.ended_at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "call_id"])
            .returning(EscalationContextRow.call_id)
        )
        claimed = (await self._session.execute(statement)).scalar_one_or_none()
        return claimed is not None

    async def get(self, user_id: UserId, call_id: CallId) -> EscalationContext | None:
        row = await self._session.get(EscalationContextRow, (user_id.value, call_id.value))
        return None if row is None else self._to_context(row)

    async def record_delivery(
        self, user_id: UserId, call_id: CallId, delivery: NotificationDelivery
    ) -> None:
        await self._session.execute(
            update(EscalationContextRow)
            .where(
                EscalationContextRow.user_id == user_id.value,
                EscalationContextRow.call_id == call_id.value,
            )
            .values(delivery=delivery.value)
        )

    async def mark_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        row = await self._session.get(
            EscalationContextRow, (user_id.value, call_id.value), with_for_update=True
        )
        if row is None:
            return False
        # Through the domain, so an end before the escalation is refused rather than stored.
        ended = self._to_context(row).ended(at_instant)
        row.status = ended.status.value
        row.ended_at = ended.ended_at
        await self._session.flush()
        return True

    @staticmethod
    def _to_context(row: EscalationContextRow) -> EscalationContext:
        return EscalationContext(
            call_id=CallId(row.call_id),
            reason=EscalationReason(row.reason),
            raised_at=row.raised_at,
            caller_label=row.caller_label,
            established=row.established,
            needed=row.needed,
            status=EscalationStatus(row.status),
            ended_at=row.ended_at,
            delivery=NotificationDelivery(row.delivery),
        )


class SqlCallReportRepository(CallReportRepository):
    """What each user's handset has reported, one row per event."""

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def record(self, user_id: UserId, report: CallReport) -> bool:
        # One statement that either inserts or does nothing, so two deliveries of the same report
        # racing each other cannot both be counted.
        result = await self._session.execute(
            insert(CallReportRow)
            .values(
                user_id=user_id.value,
                event_id=report.event_id.value,
                call_id=report.call_id.value,
                kind=report.kind.value,
                screening=None if report.screening is None else report.screening.value,
                ending=None if report.ending is None else report.ending.value,
                caller_number=None if report.caller_number is None else report.caller_number.value,
                occurred_at=report.occurred_at,
                received_at=self._clock.now(),
            )
            .on_conflict_do_nothing(index_elements=[CallReportRow.user_id, CallReportRow.event_id])
        )
        await self._session.flush()
        return _affected(result) == 1

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
