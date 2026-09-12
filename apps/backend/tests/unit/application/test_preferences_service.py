"""Changing some preferences without disturbing the others.

The rule this exists to hold: a section nobody sent is left exactly as it was. Every test here
is a version of the same failure — a screen that saves one thing and quietly erases the rest.
"""

from __future__ import annotations

from datetime import time

import pytest

from letmehandle.application.preferences.service import (
    CallHandling,
    Hours,
    PreferenceChanges,
    PreferencesService,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import ORDER, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    Formality,
    HandlingPosture,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)
from tests.contracts.preference_fakes import (
    InMemoryOnboardingRepository,
    InMemoryPreferencesRepository,
)

USER = UserId("user-1")
SOMEBODY_ELSE = UserId("user-2")
NUMBER = PhoneNumber.parse("+12025550143")

QUIET_HOURS = TimeWindow(time(22, 0), time(7, 0), "Europe/London")
WORKING_HOURS = TimeWindow(time(9, 0), time(17, 30), "Europe/London")

# Every field different from its default, so a test asserting the half that was not sent
# survived is asserting something.
REJECT_UNKNOWN = CallHandling(
    default_posture=HandlingPosture.REJECT,
    anonymous_posture=HandlingPosture.REJECT,
    posture_by_category={CallerCategory.KNOWN_CONTACT: HandlingPosture.PASS_THROUGH},
    blocked_categories=frozenset({CallerCategory.SPAM}),
    escalate_at_or_above=CallImportance.URGENT,
)


@pytest.fixture
def preferences() -> InMemoryPreferencesRepository:
    return InMemoryPreferencesRepository()


@pytest.fixture
def onboarding() -> InMemoryOnboardingRepository:
    return InMemoryOnboardingRepository()


@pytest.fixture
def service(
    preferences: InMemoryPreferencesRepository, onboarding: InMemoryOnboardingRepository
) -> PreferencesService:
    return PreferencesService(preferences=preferences, onboarding=onboarding)


class TestReading:
    async def test_somebody_who_has_chosen_nothing_gets_the_defaults(
        self, service: PreferencesService
    ) -> None:
        assert await service.get(USER) == UserPreferences()

    async def test_what_was_stored_comes_back(self, service: PreferencesService) -> None:
        await service.replace_all(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(USER)).locale == "en-GB"


class TestPartialUpdates:
    async def test_a_section_that_was_sent_changes(self, service: PreferencesService) -> None:
        updated = await service.apply(USER, PreferenceChanges(locale="en-GB"))
        assert updated.locale == "en-GB"

    async def test_a_section_that_was_not_sent_is_left_alone(
        self, service: PreferencesService
    ) -> None:
        # The whole point. A screen saving one section has no idea what the others hold.
        await service.apply(
            USER,
            PreferenceChanges(
                authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE),
                formality=Formality.WARM,
            ),
        )

        await service.apply(USER, PreferenceChanges(locale="en-GB"))

        after = await service.get(USER)
        assert after.locale == "en-GB"
        assert after.authority.allows(Capability.TAKE_A_MESSAGE)
        assert after.formality is Formality.WARM

    async def test_every_section_survives_a_change_to_another(
        self, service: PreferencesService
    ) -> None:
        # Driven over all of them, because the one that gets forgotten is never the one somebody
        # thought to write a test for.
        full = PreferenceChanges(
            locale="en-GB",
            formality=Formality.FORMAL,
            verbosity=Verbosity.BRIEF,
            authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE),
            notifications=NotificationPreferences(on_handled_call=True),
            topics=frozenset({Topic("school run")}),
            important_contacts=(ImportantContact(number=NUMBER, label="Mum"),),
            call_handling=REJECT_UNKNOWN,
        )
        await service.replace_all(USER, full)

        await service.apply(USER, PreferenceChanges(verbosity=Verbosity.DETAILED))

        after = await service.get(USER)
        assert after.verbosity is Verbosity.DETAILED
        assert after.locale == "en-GB"
        assert after.formality is Formality.FORMAL
        assert after.authority.allows(Capability.TAKE_A_MESSAGE)
        assert after.notifications.on_handled_call
        assert after.topics == frozenset({Topic("school run")})
        assert after.important_contacts == full.important_contacts
        assert after.rules.default_posture is HandlingPosture.REJECT

    async def test_an_empty_value_clears_a_section_where_absence_would_not(
        self, service: PreferencesService
    ) -> None:
        # Absent means "leave it"; empty means "clear it". Without both, a user can add a
        # contact and never remove the last one.
        await service.apply(
            USER,
            PreferenceChanges(important_contacts=(ImportantContact(number=NUMBER, label="Mum"),)),
        )

        await service.apply(USER, PreferenceChanges(important_contacts=()))

        assert (await service.get(USER)).important_contacts == ()

    async def test_a_request_that_changes_nothing_costs_no_write(
        self, service: PreferencesService, preferences: InMemoryPreferencesRepository
    ) -> None:
        # A client sending an untouched form is ordinary, and should not be a write.
        await service.apply(USER, PreferenceChanges())
        assert preferences.writes == 0

    async def test_changes_are_per_user(self, service: PreferencesService) -> None:
        await service.apply(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(SOMEBODY_ELSE)).locale == "en"


class TestCallHandlingAndHours:
    """One domain object, two screens, and the bug that lives between them.

    `CallRules` carries both how a caller is routed and when the user is available. They are
    edited on separate screens, so a save carrying one of them must not disturb the other — and
    the obvious implementation, building a whole `CallRules` from whichever half arrived,
    silently resets the other to its defaults.
    """

    async def test_setting_hours_leaves_call_handling_alone(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(call_handling=REJECT_UNKNOWN))

        await service.apply(USER, PreferenceChanges(hours=Hours(quiet=QUIET_HOURS)))

        after = await service.get(USER)
        assert after.rules.quiet_hours == QUIET_HOURS
        # Every field of the half that was not sent, because resetting any one of them is the
        # same defect wearing a different name.
        assert after.rules.default_posture is HandlingPosture.REJECT
        assert after.rules.anonymous_posture is HandlingPosture.REJECT
        assert after.rules.blocked_categories == frozenset({CallerCategory.SPAM})
        assert after.rules.escalate_at_or_above is CallImportance.URGENT

    async def test_setting_call_handling_leaves_hours_alone(
        self, service: PreferencesService
    ) -> None:
        # The direction that matters most: quiet hours that silently disappear mean a phone
        # ringing at three in the morning with nothing anywhere to say why.
        await service.apply(
            USER, PreferenceChanges(hours=Hours(working=WORKING_HOURS, quiet=QUIET_HOURS))
        )

        await service.apply(USER, PreferenceChanges(call_handling=REJECT_UNKNOWN))

        after = await service.get(USER)
        assert after.rules.quiet_hours == QUIET_HOURS
        assert after.rules.working_hours == WORKING_HOURS

    async def test_hours_can_still_be_cleared(self, service: PreferencesService) -> None:
        # Absent leaves alone; present-and-empty clears. Without both, quiet hours once set
        # could never be removed.
        await service.apply(USER, PreferenceChanges(hours=Hours(quiet=QUIET_HOURS)))

        await service.apply(USER, PreferenceChanges(hours=Hours()))

        assert (await service.get(USER)).rules.quiet_hours is None


class TestReplacing:
    async def test_it_stores_everything_it_was_given(self, service: PreferencesService) -> None:
        await service.replace_all(
            USER, PreferenceChanges(hours=Hours(quiet=QUIET_HOURS), call_handling=REJECT_UNKNOWN)
        )

        after = await service.get(USER)
        assert after.rules.quiet_hours == QUIET_HOURS
        assert after.rules.blocked_categories == frozenset({CallerCategory.SPAM})

    async def test_it_replaces_rather_than_merges(self, service: PreferencesService) -> None:
        # The deliberate counterpart to a partial update: a caller that means to set everything
        # says so, rather than relying on having mentioned every section.
        await service.apply(USER, PreferenceChanges(locale="en-GB"))
        await service.replace_all(USER, PreferenceChanges())
        assert (await service.get(USER)).locale == "en"

    async def test_it_resets_a_section_it_does_not_mention(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(hours=Hours(quiet=QUIET_HOURS)))
        await service.replace_all(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(USER)).rules.quiet_hours is None


class TestOnboarding:
    async def test_somebody_who_has_never_started_is_at_the_beginning(
        self, service: PreferencesService
    ) -> None:
        progress = await service.progress(USER)
        assert progress.next_step is ORDER[0]
        assert not progress.is_complete

    async def test_answering_a_step_moves_to_the_next(self, service: PreferencesService) -> None:
        progress = await service.record_step(USER, OnboardingStep.INTRODUCTION)
        assert progress.next_step is OnboardingStep.CALL_HANDLING

    async def test_progress_is_remembered(self, service: PreferencesService) -> None:
        # Held on the server so that reinstalling resumes rather than starting again.
        await service.record_step(USER, OnboardingStep.INTRODUCTION)
        assert (await service.progress(USER)).next_step is OnboardingStep.CALL_HANDLING

    async def test_a_skipped_step_is_not_asked_again(self, service: PreferencesService) -> None:
        await service.record_step(USER, OnboardingStep.INTRODUCTION)
        await service.record_step(USER, OnboardingStep.CALL_HANDLING)
        progress = await service.record_step(USER, OnboardingStep.IMPORTANT_CONTACTS, skipped=True)
        assert progress.next_step is OnboardingStep.HOURS

    async def test_answering_a_step_that_was_skipped_records_it_as_answered(
        self, service: PreferencesService
    ) -> None:
        # Somebody who came back to fill in what they skipped has answered it.
        await service.record_step(USER, OnboardingStep.HOURS, skipped=True)
        progress = await service.record_step(USER, OnboardingStep.HOURS)
        assert OnboardingStep.HOURS in progress.completed
        assert OnboardingStep.HOURS not in progress.skipped

    async def test_call_handling_cannot_be_skipped(self, service: PreferencesService) -> None:
        # There is no safe default for what to do with a call from somebody unknown, and
        # guessing on the user's behalf is the one thing this product must not do.
        with pytest.raises(InvariantError, match="cannot be skipped"):
            await service.record_step(USER, OnboardingStep.CALL_HANDLING, skipped=True)

    async def test_finishing_every_step_completes_it(self, service: PreferencesService) -> None:
        for step in ORDER:
            progress = await service.record_step(USER, step)
        assert progress.is_complete
        assert progress.next_step is None

    async def test_progress_is_per_user(self, service: PreferencesService) -> None:
        await service.record_step(USER, OnboardingStep.INTRODUCTION)
        assert (await service.progress(SOMEBODY_ELSE)).next_step is ORDER[0]
