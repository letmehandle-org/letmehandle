"""Changing some preferences without disturbing the others (D-023)."""

from __future__ import annotations

from datetime import time

import pytest

from letmehandle.application.preferences.service import (
    CallHandling,
    Hours,
    PersonaVoice,
    PreferenceChanges,
    PreferencesService,
)
from letmehandle.domain.errors import InvariantError, StepNotAskedError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingFlow, OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    Formality,
    HandlingPosture,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)
from letmehandle.domain.models.voice import VoiceSelection
from tests.contracts.preference_fakes import (
    InMemoryOnboardingRepository,
    InMemoryPreferencesRepository,
)

USER = UserId("user-1")
SOMEBODY_ELSE = UserId("user-2")
NUMBER = PhoneNumber.parse("+12025550143")

NOT_FORWARDED = OnboardingFlow(calls_are_forwarded=False)
FORWARDED = OnboardingFlow(calls_are_forwarded=True)

ACTIVE_HOURS = TimeWindow(time(7, 0), time(22, 0), "Europe/London")

# Every field differs from its default.
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
    return PreferencesService(
        preferences=preferences, onboarding=onboarding, onboarding_flow=NOT_FORWARDED
    )


@pytest.fixture
def forwarded(
    preferences: InMemoryPreferencesRepository, onboarding: InMemoryOnboardingRepository
) -> PreferencesService:
    """The same storage, on a deployment whose calls arrive forwarded."""
    return PreferencesService(
        preferences=preferences, onboarding=onboarding, onboarding_flow=FORWARDED
    )


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
        # Driven over every section.
        full = PreferenceChanges(
            locale="en-GB",
            formality=Formality.FORMAL,
            verbosity=Verbosity.BRIEF,
            authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE),
            notifications=NotificationPreferences(on_handled_call=True),
            topics=frozenset({Topic("school run")}),
            important_contacts=(ImportantContact(number=NUMBER, label="Mum"),),
            call_handling=REJECT_UNKNOWN,
            persona_voice=PersonaVoice("ava"),
            transcript_retention_days=30,
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
        assert after.voice.persona_voice_id == "ava"
        assert after.transcript_retention_days == 30

    async def test_an_empty_value_clears_a_section_where_absence_would_not(
        self, service: PreferencesService
    ) -> None:
        # Absent leaves alone; empty clears (D-023).
        await service.apply(
            USER,
            PreferenceChanges(important_contacts=(ImportantContact(number=NUMBER, label="Mum"),)),
        )

        await service.apply(USER, PreferenceChanges(important_contacts=()))

        assert (await service.get(USER)).important_contacts == ()

    async def test_a_request_that_changes_nothing_costs_no_write(
        self, service: PreferencesService, preferences: InMemoryPreferencesRepository
    ) -> None:
        await service.apply(USER, PreferenceChanges())
        assert preferences.writes == 0

    async def test_changes_are_per_user(self, service: PreferencesService) -> None:
        await service.apply(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(SOMEBODY_ELSE)).locale == "en"


class TestCallHandlingAndHours:
    """Routing and hours share one `CallRules`, saved from separate screens independently."""

    async def test_setting_hours_leaves_call_handling_alone(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(call_handling=REJECT_UNKNOWN))

        await service.apply(USER, PreferenceChanges(hours=Hours(active=ACTIVE_HOURS)))

        after = await service.get(USER)
        assert after.rules.active_hours == ACTIVE_HOURS
        # Every field of the half that was not sent.
        assert after.rules.default_posture is HandlingPosture.REJECT
        assert after.rules.anonymous_posture is HandlingPosture.REJECT
        assert after.rules.blocked_categories == frozenset({CallerCategory.SPAM})
        assert after.rules.escalate_at_or_above is CallImportance.URGENT

    async def test_setting_call_handling_leaves_hours_alone(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(hours=Hours(active=ACTIVE_HOURS)))

        await service.apply(USER, PreferenceChanges(call_handling=REJECT_UNKNOWN))

        after = await service.get(USER)
        assert after.rules.active_hours == ACTIVE_HOURS

    async def test_hours_can_still_be_cleared(self, service: PreferencesService) -> None:
        # Absent leaves alone; present and empty clears.
        await service.apply(USER, PreferenceChanges(hours=Hours(active=ACTIVE_HOURS)))

        await service.apply(USER, PreferenceChanges(hours=Hours()))

        assert (await service.get(USER)).rules.active_hours is None


class TestVersion:
    async def test_a_change_leaves_the_set_at_today_s_version(
        self, service: PreferencesService
    ) -> None:
        # What is handed back matches the row it becomes.
        updated = await service.apply(USER, PreferenceChanges(locale="en-GB"))
        assert updated.version == PREFERENCES_VERSION


class TestVoice:
    async def test_a_chosen_voice_is_kept(self, service: PreferencesService) -> None:
        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice("ava")))
        assert (await service.get(USER)).voice.persona_voice_id == "ava"

    async def test_changing_something_else_leaves_the_voice_alone(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice("ava")))

        await service.apply(USER, PreferenceChanges(formality=Formality.WARM))

        assert (await service.get(USER)).voice.persona_voice_id == "ava"

    async def test_a_voice_can_be_cleared(self, service: PreferencesService) -> None:
        # Back to the provider's default.
        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice("ava")))

        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice()))

        assert (await service.get(USER)).voice == VoiceSelection()


class TestReplacing:
    async def test_it_stores_everything_it_was_given(self, service: PreferencesService) -> None:
        await service.replace_all(
            USER, PreferenceChanges(hours=Hours(active=ACTIVE_HOURS), call_handling=REJECT_UNKNOWN)
        )

        after = await service.get(USER)
        assert after.rules.active_hours == ACTIVE_HOURS
        assert after.rules.blocked_categories == frozenset({CallerCategory.SPAM})

    async def test_it_replaces_rather_than_merges(self, service: PreferencesService) -> None:
        await service.apply(USER, PreferenceChanges(locale="en-GB"))
        await service.replace_all(USER, PreferenceChanges())
        assert (await service.get(USER)).locale == "en"

    async def test_it_keeps_the_voice_it_cannot_carry(self, service: PreferencesService) -> None:
        # The voice survives: a replace request has no field for it.
        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice("ava")))

        await service.replace_all(USER, PreferenceChanges(locale="en-GB"))

        assert (await service.get(USER)).voice.persona_voice_id == "ava"

    async def test_a_replace_that_does_carry_a_voice_uses_it(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(persona_voice=PersonaVoice("ava")))

        await service.replace_all(USER, PreferenceChanges(persona_voice=PersonaVoice("noah")))

        assert (await service.get(USER)).voice.persona_voice_id == "noah"

    async def test_it_resets_a_section_it_does_not_mention(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(hours=Hours(active=ACTIVE_HOURS)))
        await service.replace_all(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(USER)).rules.active_hours is None


class TestOnboarding:
    async def test_somebody_who_has_never_started_is_at_the_beginning(
        self, service: PreferencesService
    ) -> None:
        progress = await service.progress(USER)
        assert progress.next_step is OnboardingStep.CALL_HANDLING
        assert not progress.is_complete

    async def test_answering_a_step_moves_to_the_next(self, service: PreferencesService) -> None:
        progress = await service.record_step(USER, OnboardingStep.CALL_HANDLING)
        assert progress.next_step is OnboardingStep.HOURS

    async def test_progress_is_remembered(self, service: PreferencesService) -> None:
        await service.record_step(USER, OnboardingStep.CALL_HANDLING)
        assert (await service.progress(USER)).next_step is OnboardingStep.HOURS

    async def test_a_skipped_step_is_not_asked_again(self, service: PreferencesService) -> None:
        await service.record_step(USER, OnboardingStep.CALL_HANDLING)
        progress = await service.record_step(USER, OnboardingStep.HOURS, skipped=True)
        assert progress.next_step is OnboardingStep.AUTHORITY

    async def test_answering_a_step_that_was_skipped_records_it_as_answered(
        self, service: PreferencesService
    ) -> None:
        await service.record_step(USER, OnboardingStep.HOURS, skipped=True)
        progress = await service.record_step(USER, OnboardingStep.HOURS)
        assert OnboardingStep.HOURS in progress.completed
        assert OnboardingStep.HOURS not in progress.skipped

    async def test_call_handling_cannot_be_skipped(self, service: PreferencesService) -> None:
        with pytest.raises(InvariantError, match="cannot be skipped"):
            await service.record_step(USER, OnboardingStep.CALL_HANDLING, skipped=True)

    async def test_finishing_every_step_completes_it(self, service: PreferencesService) -> None:
        for step in NOT_FORWARDED.steps:
            progress = await service.record_step(USER, step)
        assert progress.is_complete
        assert progress.next_step is None

    async def test_progress_is_per_user(self, service: PreferencesService) -> None:
        await service.record_step(USER, OnboardingStep.CALL_HANDLING)
        assert (await service.progress(SOMEBODY_ELSE)).next_step is OnboardingStep.CALL_HANDLING


class TestForwardingStep:
    async def test_where_calls_are_forwarded_it_follows_call_handling(
        self, forwarded: PreferencesService
    ) -> None:
        progress = await forwarded.record_step(USER, OnboardingStep.CALL_HANDLING)
        assert progress.next_step is OnboardingStep.CALL_FORWARDING

    async def test_where_it_is_not_asked_recording_it_is_refused_and_nothing_is_stored(
        self, service: PreferencesService, onboarding: InMemoryOnboardingRepository
    ) -> None:
        with pytest.raises(StepNotAskedError):
            await service.record_step(USER, OnboardingStep.CALL_FORWARDING)
        assert await onboarding.get(USER) == OnboardingProgress()

    async def test_an_answer_given_where_it_was_asked_is_kept_but_not_shown_elsewhere(
        self,
        service: PreferencesService,
        forwarded: PreferencesService,
        onboarding: InMemoryOnboardingRepository,
    ) -> None:
        await forwarded.record_step(USER, OnboardingStep.CALL_FORWARDING)

        assert (await service.progress(USER)).completed == ()
        assert (await forwarded.progress(USER)).completed == (OnboardingStep.CALL_FORWARDING,)


class TestTranscriptRetention:
    async def test_it_starts_at_seven_days(self, service: PreferencesService) -> None:
        assert (await service.get(USER)).transcript_retention_days == 7

    async def test_a_change_is_stored_and_leaves_the_rest_alone(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(locale="en-GB"))
        await service.apply(USER, PreferenceChanges(transcript_retention_days=1))

        after = await service.get(USER)
        assert after.transcript_retention_days == 1
        assert after.locale == "en-GB"

    async def test_changing_something_else_keeps_it(self, service: PreferencesService) -> None:
        await service.apply(USER, PreferenceChanges(transcript_retention_days=90))
        await service.apply(USER, PreferenceChanges(verbosity=Verbosity.BRIEF))
        assert (await service.get(USER)).transcript_retention_days == 90

    async def test_a_replace_that_does_not_mention_it_resets_it(
        self, service: PreferencesService
    ) -> None:
        await service.apply(USER, PreferenceChanges(transcript_retention_days=90))
        await service.replace_all(USER, PreferenceChanges(locale="en-GB"))
        assert (await service.get(USER)).transcript_retention_days == 7

    async def test_a_value_outside_the_bounds_is_refused(self, service: PreferencesService) -> None:
        with pytest.raises(InvariantError):
            await service.apply(USER, PreferenceChanges(transcript_retention_days=91))
