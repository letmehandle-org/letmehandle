"""Preferences through a real database, and back out unchanged."""

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
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    CallRules,
    DisclosableFact,
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
from letmehandle.domain.models.voice import VoiceSelection
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
    """A preference set with every field set to something other than its default."""
    return UserPreferences(
        locale="en-GB",
        formality=Formality.FORMAL,
        verbosity=Verbosity.BRIEF,
        topics=frozenset({Topic("school run"), Topic("deliveries")}),
        disclosable_facts=frozenset({DisclosableFact("Works from home on Tuesdays")}),
        authority=AgentAuthority.granting(
            Capability.TAKE_A_MESSAGE, Capability.SHARE_DELIVERY_INSTRUCTIONS
        ),
        notifications=NotificationPreferences(
            on_handled_call=True,
            on_blocked_call=True,
            on_missed_escalation=False,
            daily_summary=True,
            respect_active_hours=False,
        ),
        important_contacts=(
            ImportantContact(number=NUMBER, label="Mum"),
            ImportantContact(
                number=ANOTHER, label="The school", posture=HandlingPosture.HANDLE_WITH_AGENT
            ),
        ),
        voice=VoiceSelection(cloned_voice_id="a-clone", persona_voice_id="ava"),
        transcript_retention_days=30,
        rules=CallRules(
            default_posture=HandlingPosture.REJECT,
            anonymous_posture=HandlingPosture.PASS_THROUGH,
            posture_by_category={CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT},
            blocked_categories=frozenset({CallerCategory.SPAM}),
            escalate_at_or_above=CallImportance.URGENT,
            active_hours=TimeWindow(time(7, 0), time(22, 0), "Europe/London"),
        ),
    )


async def a_user(session: AsyncSession, user_id: UserId, number: PhoneNumber) -> None:
    await SqlUserRepository(session, FixedClock(NOW)).add(User(id=user_id, phone_number=number))


class TestMapping:
    def test_everything_survives_a_round_trip(self) -> None:
        original = everything()
        assert document_to_preferences(preferences_to_document(original)) == original

    def test_the_document_is_the_same_every_time(self) -> None:
        # Frozensets are sorted, so identical preference sets produce identical documents.
        assert preferences_to_document(everything()) == preferences_to_document(everything())

    def test_a_document_from_an_older_version_reads_with_today_s_defaults(self) -> None:
        # What a document lacks is supplied from the defaults.
        sparse = {"version": 1, "locale": "en"}
        read = document_to_preferences(sparse)
        assert read.formality is Formality.NEUTRAL
        assert read.notifications == NotificationPreferences()
        assert read.rules.escalate_at_or_above is CallImportance.NOTABLE
        # No stored voice resolves to the provider's default voice.
        assert read.voice == VoiceSelection()

    def test_a_document_from_before_retention_was_a_setting_keeps_seven_days(self) -> None:
        document = preferences_to_document(everything())
        del document["transcript_retention_days"]
        document["version"] = 2
        assert document_to_preferences(document).transcript_retention_days == 7

    @pytest.mark.parametrize(("stored", "read"), [(0, 1), (-5, 1), (45, 45)])
    def test_a_retention_below_the_floor_is_raised_to_it(self, stored: int, read: int) -> None:
        # Rounding up keeps a transcript longer, which is recoverable.
        document = preferences_to_document(everything())
        document["transcript_retention_days"] = stored
        assert document_to_preferences(document).transcript_retention_days == read

    def test_a_retention_above_the_ceiling_is_kept_as_stored(self) -> None:
        # A higher ceiling from another deployment is honoured, so nothing is deleted early.
        document = preferences_to_document(everything())
        document["transcript_retention_days"] = 365

        read = document_to_preferences(document)

        assert read.transcript_retention_days == 365
        assert preferences_to_document(read)["transcript_retention_days"] == 365

    @pytest.mark.parametrize("stored", ["7", 7.5, True, {"days": 7}])
    def test_a_retention_that_is_not_a_whole_number_is_corruption(self, stored: object) -> None:
        document = preferences_to_document(everything())
        document["transcript_retention_days"] = stored
        with pytest.raises(InvariantError, match="retention"):
            document_to_preferences(document)

    def test_a_voice_stored_as_something_other_than_a_name_reads_as_no_choice(self) -> None:
        # An unusable identifier resolves to the default voice rather than refusing the load.
        document = preferences_to_document(everything())
        document["voice"] = {"cloned": 7, "persona": "   "}

        assert document_to_preferences(document).voice == VoiceSelection()

    def test_an_unrecognised_value_is_dropped_rather_than_fatal(self) -> None:
        # Values from a newer deployment are dropped so the settings still load.
        document = preferences_to_document(everything())
        document["authority"].append("fly_the_user_to_the_moon")
        document["rules"]["blocked_categories"].append("something_invented")

        read = document_to_preferences(document)

        assert read.authority.allows(Capability.TAKE_A_MESSAGE)
        assert read.rules.blocked_categories == frozenset({CallerCategory.SPAM})

    def test_a_number_that_cannot_be_read_is_refused(self) -> None:
        # A malformed number is corruption, unlike an unknown enum value.
        document = preferences_to_document(everything())
        document["important_contacts"][0]["number"] = "not-a-number"
        with pytest.raises(InvariantError, match=r"E\.164"):
            document_to_preferences(document)

    def test_the_version_is_recorded(self) -> None:
        assert preferences_to_document(everything())["version"] == PREFERENCES_VERSION

    def test_a_document_read_at_an_older_version_is_written_back_at_this_one(self) -> None:
        # The version field describes the document's shape, so it is always the current one.
        older = preferences_to_document(everything())
        older["version"] = 1

        rewritten = preferences_to_document(document_to_preferences(older))

        assert rewritten["version"] == PREFERENCES_VERSION


class TestReadingHoursWrittenBeforeD027:
    """Version 2 said when not to be reached; version 4 says when the assistant answers."""

    def older(self, rules: dict[str, object]) -> dict[str, object]:
        document = preferences_to_document(everything())
        document["version"] = 2
        del document["rules"]["active_hours"]
        document["rules"].update(rules)
        return document

    def test_quiet_hours_are_read_as_around_the_clock(self) -> None:
        # Quiet hours inverted into assistant hours would ring the user through those hours.
        read = document_to_preferences(
            self.older({"quiet_hours": {"start": "22:00", "end": "07:00", "zone": "Europe/London"}})
        )
        assert read.rules.active_hours is None

    def test_no_quiet_hours_is_around_the_clock(self) -> None:
        assert document_to_preferences(self.older({"quiet_hours": None})).rules.active_hours is None

    def test_working_hours_are_read_as_around_the_clock_too(self) -> None:
        # Working hours changed no decision, so the default applies.
        read = document_to_preferences(
            self.older(
                {"working_hours": {"start": "09:00", "end": "17:30", "zone": "Europe/London"}}
            )
        )
        assert read.rules.active_hours is None

    def test_the_older_notification_flag_is_read(self) -> None:
        document = self.older({})
        document["notifications"] = {"respect_quiet_hours": False}
        assert not document_to_preferences(document).notifications.respect_active_hours

    def test_a_document_that_says_active_hours_is_read_by_what_it_says(self) -> None:
        # A current document carrying an older key: the newer field wins.
        document = preferences_to_document(everything())
        document["rules"]["quiet_hours"] = {"start": "01:00", "end": "02:00", "zone": "UTC"}
        assert (
            document_to_preferences(document).rules.active_hours == everything().rules.active_hours
        )

    def test_broken_quiet_hours_are_still_corruption(self) -> None:
        with pytest.raises(InvariantError, match="stored hours"):
            document_to_preferences(self.older({"quiet_hours": {"start": "22:00"}}))


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
        # One statement, so concurrent saves of different sections both persist.
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
            completed=frozenset({OnboardingStep.CALL_HANDLING}),
            skipped=frozenset({OnboardingStep.HOURS}),
        )

        await repository.save(USER, progress)

        assert await repository.get(USER) == progress

    async def test_saving_twice_replaces(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        repository = SqlOnboardingRepository(session, FixedClock(NOW))

        await repository.save(
            USER, OnboardingProgress(completed=frozenset({OnboardingStep.CALL_HANDLING}))
        )
        await repository.save(
            USER,
            OnboardingProgress(
                completed=frozenset({OnboardingStep.CALL_HANDLING, OnboardingStep.HOURS})
            ),
        )

        stored = await repository.get(USER)
        assert stored.completed == frozenset({OnboardingStep.CALL_HANDLING, OnboardingStep.HOURS})

    async def test_a_step_this_version_does_not_know_is_dropped(
        self, session: AsyncSession
    ) -> None:
        # Removed steps (D-032) and steps from a newer deployment are both ignored.
        from sqlalchemy import insert

        from letmehandle.adapters.database.models import OnboardingRow

        await a_user(session, USER, NUMBER)
        await session.execute(
            insert(OnboardingRow).values(
                user_id=USER.value,
                completed=["introduction", "call_handling", "a_step_from_the_future"],
                skipped=["important_contacts", "personality"],
                updated_at=NOW,
            )
        )
        await session.flush()

        progress = await SqlOnboardingRepository(session, FixedClock(NOW)).get(USER)

        assert progress.completed == frozenset({OnboardingStep.CALL_HANDLING})
        assert progress.skipped == frozenset()

    async def test_progress_is_per_user(self, session: AsyncSession) -> None:
        await a_user(session, USER, NUMBER)
        await a_user(session, OTHER, ANOTHER)
        repository = SqlOnboardingRepository(session, FixedClock(NOW))

        await repository.save(
            USER, OnboardingProgress(completed=frozenset({OnboardingStep.CALL_HANDLING}))
        )

        assert await repository.get(OTHER) == OnboardingProgress()


class TestMalformedDocuments:
    def test_hours_that_cannot_be_read_are_refused(self) -> None:
        # Malformed hours are corruption, refused rather than silently dropped.
        document = preferences_to_document(everything())
        document["rules"]["active_hours"]["start"] = "not a time"

        with pytest.raises(InvariantError, match="stored hours"):
            document_to_preferences(document)

    def test_hours_missing_a_field_are_refused(self) -> None:
        document = preferences_to_document(everything())
        del document["rules"]["active_hours"]["zone"]

        with pytest.raises(InvariantError, match="stored hours"):
            document_to_preferences(document)

    @pytest.mark.parametrize("stored", [99, "urgent", None, True])
    def test_an_unreadable_escalation_threshold_falls_back(self, stored: object) -> None:
        # An unrecognised threshold becomes the default rather than failing the load.
        document = preferences_to_document(everything())
        document["rules"]["escalate_at_or_above"] = stored

        assert document_to_preferences(document).rules.escalate_at_or_above is (
            CallImportance.NOTABLE
        )


# Every section stored as the wrong kind of value, each refused with a named error.
MALFORMED_SHAPES: list[dict[str, object]] = [
    {"notifications": "yes"},
    {"rules": []},
    {"voice": "abc"},
    {"topics": [1]},
    {"topics": "work"},
    {"disclosable_facts": [None]},
    {"version": "x"},
    {"version": True},
    {"locale": ["en"]},
    {"authority": 5},
    {"important_contacts": {"number": "+12025550143"}},
    {"important_contacts": ["+12025550143"]},
    {"important_contacts": [{"number": 12025550143, "label": "Home"}]},
    {"important_contacts": [{"number": "+12025550143", "label": 7}]},
    {"rules": {"posture_by_category": ["spam"]}},
    {"rules": {"blocked_categories": "spam"}},
    {"rules": {"quiet_hours": "22:00-07:00"}},
    {"rules": {"quiet_hours": {"start": 22, "end": "07:00", "zone": "UTC"}}},
]


class TestMalformedShapes:
    @pytest.mark.parametrize("document", MALFORMED_SHAPES, ids=repr)
    def test_a_section_of_the_wrong_shape_is_the_domain_error(
        self, document: dict[str, object]
    ) -> None:
        with pytest.raises(InvariantError):
            document_to_preferences(document)

    @pytest.mark.parametrize("document", [[], "preferences", 7], ids=repr)
    def test_a_document_that_is_not_an_object_is_the_domain_error(self, document: object) -> None:
        with pytest.raises(InvariantError):
            document_to_preferences(document)


class TestCorruptEntries:
    def test_a_contact_missing_its_number_raises_the_domain_error(self) -> None:
        # Not a bare KeyError: that escapes as a server fault with nothing naming the cause.
        document = preferences_to_document(everything())
        del document["important_contacts"][0]["number"]

        with pytest.raises(InvariantError, match="missing"):
            document_to_preferences(document)

    def test_a_contact_missing_its_label_raises_too(self) -> None:
        document = preferences_to_document(everything())
        del document["important_contacts"][0]["label"]

        with pytest.raises(InvariantError, match="missing"):
            document_to_preferences(document)

    def test_an_empty_window_document_is_corruption_rather_than_no_window(self) -> None:
        # `None` is a user who set no hours; `{}` is a row that lost them.
        document = preferences_to_document(everything())
        document["rules"]["active_hours"] = {}

        with pytest.raises(InvariantError, match="stored hours"):
            document_to_preferences(document)
