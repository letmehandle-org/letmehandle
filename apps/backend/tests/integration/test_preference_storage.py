"""Preferences through a real database, and back out unchanged.

A document that survives a round trip is the whole promise of storing one. Every test here is a
version of the failure that breaks it: a field written and not read, a set that comes back in a
different order, or a value from an older deployment that today's code cannot make sense of.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.database.preference_mapping import (
    document_to_preferences,
    preferences_to_document,
)
from letmehandle.adapters.database.repositories import (
    SqlOnboardingRepository,
    SqlPreferencesRepository,
    SqlUserRepository,
)
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    CallRules,
    Formality,
    HandlingPosture,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)
from letmehandle.domain.models.user import User
from tests.contracts.fakes import FixedClock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
USER = UserId("user-1")
OTHER = UserId("user-2")
NUMBER = PhoneNumber.parse("+12025550143")
ANOTHER = PhoneNumber.parse("+12025550144")


def everything() -> UserPreferences:
    """A preference set with every field set to something other than its default.

    Every field, deliberately. A round-trip test built from the defaults passes even when half
    the mapping is missing, because the default is what a missing field produces.
    """
    return UserPreferences(
        locale="en-GB",
        formality=Formality.FORMAL,
        verbosity=Verbosity.BRIEF,
        topics=frozenset({Topic("school run"), Topic("deliveries")}),
        disclosable_facts=frozenset({"works from home"}),
        authority=AgentAuthority.granting(
            Capability.TAKE_A_MESSAGE, Capability.SHARE_DELIVERY_INSTRUCTIONS
        ),
        notifications=NotificationPreferences(
            on_handled_call=True,
            on_blocked_call=True,
            on_missed_escalation=False,
            daily_summary=True,
            respect_quiet_hours=False,
        ),
        important_contacts=(
            ImportantContact(number=NUMBER, label="Mum"),
            ImportantContact(
                number=ANOTHER, label="The school", posture=HandlingPosture.HANDLE_WITH_AGENT
            ),
        ),
        rules=CallRules(
            default_posture=HandlingPosture.REJECT,
            anonymous_posture=HandlingPosture.PASS_THROUGH,
            posture_by_category={CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT},
            blocked_categories=frozenset({CallerCategory.SPAM}),
            escalate_at_or_above=CallImportance.URGENT,
            working_hours=TimeWindow(time(9, 0), time(17, 30), "Europe/London"),
            quiet_hours=TimeWindow(time(22, 0), time(7, 0), "Europe/London"),
        ),
    )


async def a_user(session: AsyncSession, user_id: UserId, number: PhoneNumber) -> None:
    await SqlUserRepository(session, FixedClock(NOW)).add(User(id=user_id, phone_number=number))


class TestMapping:
    def test_everything_survives_a_round_trip(self) -> None:
        original = everything()
        assert document_to_preferences(preferences_to_document(original)) == original

    def test_the_document_is_the_same_every_time(self) -> None:
        # A frozenset iterates in hash order, which differs between processes. Without sorting,
        # two identical preference sets produce different documents and every save looks like a
        # change.
        assert preferences_to_document(everything()) == preferences_to_document(everything())

    def test_a_document_from_an_older_version_reads_with_today_s_defaults(self) -> None:
        # What the version field beside it is for: what a reader cannot supply from the
        # document it supplies from the defaults.
        sparse = {"version": 1, "locale": "en"}
        read = document_to_preferences(sparse)
        assert read.formality is Formality.NEUTRAL
        assert read.notifications == NotificationPreferences()
        assert read.rules.escalate_at_or_above is CallImportance.NOTABLE

    def test_an_unrecognised_value_is_dropped_rather_than_fatal(self) -> None:
        # Written by a newer deployment. Refusing to load somebody's settings over a field they
        # never set would lock them out of their own account.
        document = preferences_to_document(everything())
        document["authority"].append("fly_the_user_to_the_moon")
        document["rules"]["blocked_categories"].append("something_invented")

        read = document_to_preferences(document)

        assert read.authority.allows(Capability.TAKE_A_MESSAGE)
        assert read.rules.blocked_categories == frozenset({CallerCategory.SPAM})

    def test_a_number_that_cannot_be_read_is_refused(self) -> None:
        # Distinct from an unknown enum: a malformed number is corruption, not a newer version,
        # and silently dropping a contact would change who gets through.
        document = preferences_to_document(everything())
        document["important_contacts"][0]["number"] = "not-a-number"
        with pytest.raises(Exception, match="E.164"):
            document_to_preferences(document)

    def test_the_version_is_recorded(self) -> None:
        assert preferences_to_document(everything())["version"] == PREFERENCES_VERSION


class TestPreferencesRepository:
    async def test_nothing_stored_is_nothing(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        assert await SqlPreferencesRepository(session, FixedClock(NOW)).get(USER) is None

    async def test_a_full_set_round_trips_through_postgres(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        repository = SqlPreferencesRepository(session, FixedClock(NOW))

        await repository.save(USER, everything())

        assert await repository.get(USER) == everything()

    async def test_saving_twice_replaces_rather_than_failing(self, session: AsyncSession) -> None:
        # One statement, so that two requests saving different sections at the same time cannot
        # leave one of them with nothing to show for it.
        await a_user(session, USER, NUMBER)
        repository = SqlPreferencesRepository(session, FixedClock(NOW))

        await repository.save(USER, UserPreferences(locale="en"))
        await repository.save(USER, UserPreferences(locale="en-GB"))

        stored = await repository.get(USER)
        assert stored is not None
        assert stored.locale == "en-GB"

    async def test_one_user_cannot_read_another_s_preferences(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        await a_user(session, OTHER, ANOTHER)
        repository = SqlPreferencesRepository(session, FixedClock(NOW))

        await repository.save(USER, everything())

        assert await repository.get(OTHER) is None

    async def test_deleting_a_user_takes_their_preferences(self, session: AsyncSession) -> None:
        from sqlalchemy import delete

        from letmehandle.adapters.database.models import UserRow

        await a_user(session, USER, NUMBER)
        repository = SqlPreferencesRepository(session, FixedClock(NOW))
        await repository.save(USER, everything())

        await session.execute(delete(UserRow).where(UserRow.id == USER.value))
        await session.flush()

        assert await repository.get(USER) is None


class TestOnboardingRepository:
    async def test_somebody_who_has_never_started_is_at_the_beginning(
        self, session: AsyncSession
    ) -> None:
        await a_user(session, USER, NUMBER)
        progress = await SqlOnboardingRepository(session, FixedClock(NOW)).get(USER)
        assert progress == OnboardingProgress()

    async def test_progress_round_trips(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        repository = SqlOnboardingRepository(session, FixedClock(NOW))
        progress = OnboardingProgress(
            completed=frozenset({OnboardingStep.INTRODUCTION}),
            skipped=frozenset({OnboardingStep.HOURS}),
        )

        await repository.save(USER, progress)

        assert await repository.get(USER) == progress

    async def test_saving_twice_replaces(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        repository = SqlOnboardingRepository(session, FixedClock(NOW))

        await repository.save(
            USER, OnboardingProgress(completed=frozenset({OnboardingStep.INTRODUCTION}))
        )
        await repository.save(
            USER,
            OnboardingProgress(
                completed=frozenset({OnboardingStep.INTRODUCTION, OnboardingStep.CALL_HANDLING})
            ),
        )

        stored = await repository.get(USER)
        assert stored.next_step is OnboardingStep.IMPORTANT_CONTACTS

    async def test_a_step_this_version_does_not_know_is_dropped(
        self, session: AsyncSession
    ) -> None:
        # A step removed from the flow should not stop somebody signing in, and one added by a
        # newer deployment means nothing here. The worst outcome is being asked again.
        from sqlalchemy import insert

        from letmehandle.adapters.database.models import OnboardingRow

        await a_user(session, USER, NUMBER)
        await session.execute(
            insert(OnboardingRow).values(
                user_id=USER.value,
                completed=["introduction", "a_step_from_the_future"],
                skipped=[],
                updated_at=NOW,
            )
        )
        await session.flush()

        progress = await SqlOnboardingRepository(session, FixedClock(NOW)).get(USER)

        assert progress.completed == frozenset({OnboardingStep.INTRODUCTION})

    async def test_progress_is_per_user(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        await a_user(session, OTHER, ANOTHER)
        repository = SqlOnboardingRepository(session, FixedClock(NOW))

        await repository.save(
            USER, OnboardingProgress(completed=frozenset({OnboardingStep.INTRODUCTION}))
        )

        assert await repository.get(OTHER) == OnboardingProgress()
