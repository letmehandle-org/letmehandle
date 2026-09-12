"""Timezones are where this kind of code goes quietly wrong, so they are where the tests are."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance
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
)

NUMBER = PhoneNumber.parse("+12025550143")

LONDON = "Europe/London"
KOLKATA = "Asia/Kolkata"


def at(year: int, month: int, day: int, hour: int, minute: int = 0, zone: str = LONDON) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(zone))


class TestTimeWindow:
    def test_a_window_inside_one_day_contains_what_it_should(self) -> None:
        window = TimeWindow(time(9, 0), time(17, 0), LONDON)
        assert window.contains(at(2026, 6, 1, 12))
        assert not window.contains(at(2026, 6, 1, 8))
        assert not window.contains(at(2026, 6, 1, 18))

    def test_the_window_is_half_open(self) -> None:
        # Start inclusive, end exclusive, so two adjacent windows cannot both claim the moment
        # between them.
        window = TimeWindow(time(9, 0), time(17, 0), LONDON)
        assert window.contains(at(2026, 6, 1, 9, 0))
        assert not window.contains(at(2026, 6, 1, 17, 0))

    def test_a_window_that_wraps_past_midnight_works(self) -> None:
        # Quiet hours are the ordinary case, and they are the case a naive between gets wrong.
        quiet = TimeWindow(time(22, 0), time(7, 0), LONDON)
        assert quiet.wraps_midnight
        assert quiet.contains(at(2026, 6, 1, 23))
        assert quiet.contains(at(2026, 6, 1, 2))
        assert not quiet.contains(at(2026, 6, 1, 12))
        assert not quiet.contains(at(2026, 6, 1, 7, 0))

    def test_the_window_is_evaluated_in_its_own_zone_not_the_instant_s(self) -> None:
        # The mistake this prevents: a user's nine-to-five compared against a server's clock.
        # Half past eight in the evening in London is two in the morning in Kolkata.
        working = TimeWindow(time(9, 0), time(17, 0), KOLKATA)
        assert not working.contains(datetime(2026, 6, 1, 20, 30, tzinfo=ZoneInfo(LONDON)))
        assert working.contains(datetime(2026, 6, 1, 6, 30, tzinfo=ZoneInfo(LONDON)))

    def test_it_is_still_correct_across_a_daylight_saving_change(self) -> None:
        # London moves an hour on the last Sunday in March. A window stored as local time must
        # follow the clock, not the offset it had when it was written.
        working = TimeWindow(time(9, 0), time(17, 0), LONDON)
        before = datetime(2026, 3, 28, 10, 0, tzinfo=ZoneInfo(LONDON))
        after = datetime(2026, 3, 30, 10, 0, tzinfo=ZoneInfo(LONDON))
        assert working.contains(before)
        assert working.contains(after)
        # The same two instants expressed in UTC are an hour apart in offset, and both must
        # still be inside the user's working day.
        assert working.contains(before.astimezone(UTC))
        assert working.contains(after.astimezone(UTC))

    def test_an_instant_with_no_timezone_is_refused(self) -> None:
        window = TimeWindow(time(9, 0), time(17, 0), LONDON)
        with pytest.raises(InvariantError, match="timezone"):
            window.contains(datetime(2026, 6, 1, 12, 0))

    def test_a_window_of_no_length_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            TimeWindow(time(9, 0), time(9, 0), LONDON)

    def test_an_unknown_zone_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="timezone"):
            TimeWindow(time(9, 0), time(17, 0), "Mars/Olympus_Mons")


class TestCallRules:
    def test_the_default_posture_applies_to_anything_unspecified(self) -> None:
        rules = CallRules(default_posture=HandlingPosture.HANDLE_WITH_AGENT)
        assert rules.posture_for(CallerCategory.UNKNOWN) is HandlingPosture.HANDLE_WITH_AGENT

    def test_a_category_posture_overrides_the_default(self) -> None:
        rules = CallRules(
            posture_by_category={CallerCategory.KNOWN_CONTACT: HandlingPosture.PASS_THROUGH}
        )
        assert rules.posture_for(CallerCategory.KNOWN_CONTACT) is HandlingPosture.PASS_THROUGH
        assert rules.posture_for(CallerCategory.SALES) is HandlingPosture.HANDLE_WITH_AGENT

    def test_a_blocked_category_is_rejected(self) -> None:
        rules = CallRules(blocked_categories=frozenset({CallerCategory.SPAM}))
        assert rules.posture_for(CallerCategory.SPAM) is HandlingPosture.REJECT

    def test_a_category_cannot_be_both_blocked_and_handled(self) -> None:
        # The outcome would depend on which rule was read first, which is a coin toss dressed
        # up as configuration.
        with pytest.raises(InvariantError):
            CallRules(
                blocked_categories=frozenset({CallerCategory.SALES}),
                posture_by_category={CallerCategory.SALES: HandlingPosture.PASS_THROUGH},
            )

    def test_quiet_hours_are_absent_until_set(self) -> None:
        assert not CallRules().is_quiet_at(at(2026, 6, 1, 3))

    def test_quiet_hours_apply_when_set(self) -> None:
        rules = CallRules(quiet_hours=TimeWindow(time(22, 0), time(7, 0), LONDON))
        assert rules.is_quiet_at(at(2026, 6, 1, 3))
        assert not rules.is_quiet_at(at(2026, 6, 1, 12))

    def test_unset_working_hours_mean_unknown_rather_than_never(self) -> None:
        # A user who has not said when they work cannot be assumed unavailable, or the
        # assistant answers everything.
        assert CallRules().is_working_at(at(2026, 6, 1, 3))

    def test_the_escalation_threshold_defaults_to_notable(self) -> None:
        assert CallRules().escalate_at_or_above is CallImportance.NOTABLE


class TestUserPreferences:
    def test_the_defaults_grant_nothing_and_disclose_nothing(self) -> None:
        preferences = UserPreferences()
        assert preferences.authority == AgentAuthority.none()
        assert preferences.disclosable_facts == frozenset()
        assert preferences.formality is Formality.NEUTRAL

    def test_preferences_carry_authority_and_rules_together(self) -> None:
        preferences = UserPreferences(
            authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE),
            rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH),
        )
        assert preferences.authority.allows(Capability.TAKE_A_MESSAGE)
        assert preferences.rules.default_posture is HandlingPosture.PASS_THROUGH

    def test_a_blank_locale_is_refused(self) -> None:
        # The agent's language is configuration. A blank one means a prompt with no language.
        with pytest.raises(InvariantError):
            UserPreferences(locale="  ")


class TestTopics:
    def test_a_topic_is_normalised(self) -> None:
        # "School Run", "school run" and "  school   run " are one topic rather than three,
        # which is what stops a list nobody can maintain.
        assert Topic("  School   Run ") == Topic("school run")
        assert Topic("School Run").name == "school run"

    def test_topics_compare_and_hash_by_their_normalised_form(self) -> None:
        assert len({Topic("Deliveries"), Topic("deliveries")}) == 1

    @pytest.mark.parametrize("raw", ["", "   ", "\t", "\n"])
    def test_a_topic_with_nothing_in_it_is_refused(self, raw: str) -> None:
        with pytest.raises(InvariantError):
            Topic(raw)

    def test_a_topic_that_is_really_a_sentence_is_refused(self) -> None:
        # A sentence in a list the agent reads is an instruction, and the caller is the one
        # person who must never be able to write one.
        with pytest.raises(InvariantError, match="instruction"):
            Topic("x" * (Topic.MAX_LENGTH + 1))

    def test_a_topic_at_the_limit_is_accepted(self) -> None:
        assert len(Topic("x" * Topic.MAX_LENGTH).name) == Topic.MAX_LENGTH

    def test_a_topic_rendered_is_its_name(self) -> None:
        assert str(Topic("School Run")) == "school run"


class TestImportantContacts:
    def test_a_contact_passes_calls_through_by_default(self) -> None:
        # The point of marking somebody important: their call reaches you.
        contact = ImportantContact(number=NUMBER, label="Mum")
        assert contact.posture is HandlingPosture.PASS_THROUGH

    def test_a_contact_can_be_handled_by_the_assistant_instead(self) -> None:
        contact = ImportantContact(
            number=NUMBER, label="The school", posture=HandlingPosture.HANDLE_WITH_AGENT
        )
        assert contact.posture is HandlingPosture.HANDLE_WITH_AGENT

    @pytest.mark.parametrize("label", ["", "   "])
    def test_a_contact_without_a_label_is_refused(self, label: str) -> None:
        # Otherwise the list is numbers, and a list of numbers is not something to read.
        with pytest.raises(InvariantError):
            ImportantContact(number=NUMBER, label=label)

    def test_an_overlong_label_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            ImportantContact(number=NUMBER, label="x" * (ImportantContact.MAX_LABEL + 1))

    def test_rendering_a_contact_gives_the_label_and_not_the_number(self) -> None:
        # The number belongs to somebody who never agreed to anything.
        contact = ImportantContact(number=NUMBER, label="Mum")
        assert str(contact) == "Mum"
        assert NUMBER.value not in str(contact)


class TestNotificationPreferences:
    def test_nothing_optional_is_on_by_default(self) -> None:
        # A product that notifies about everything is one people silence, and a silenced
        # product cannot reach them when it matters.
        defaults = NotificationPreferences()
        assert not defaults.on_handled_call
        assert not defaults.on_blocked_call
        assert not defaults.daily_summary

    def test_being_told_about_an_escalation_is_not_optional(self) -> None:
        # Being told the assistant needs you, while it needs you, is the product. Turning it
        # off would leave a phone ringing with no idea why.
        assert NotificationPreferences().on_escalation

    def test_quiet_hours_are_respected_by_default(self) -> None:
        assert NotificationPreferences().respect_quiet_hours

    def test_a_missed_escalation_is_worth_knowing_about(self) -> None:
        assert NotificationPreferences().on_missed_escalation


class TestPreferenceLimits:
    def test_preferences_record_the_version_they_were_written_in(self) -> None:
        assert UserPreferences().version == PREFERENCES_VERSION

    def test_a_version_before_the_first_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="versions start at 1"):
            UserPreferences(version=0)

    def test_too_many_contacts_is_refused(self) -> None:
        # Beyond a point the list is an address book, and everything in it stops being
        # important.
        contacts = tuple(
            ImportantContact(
                number=PhoneNumber.parse(f"+1202555{number:04d}"), label=f"Contact {number}"
            )
            for number in range(UserPreferences.MAX_CONTACTS + 1)
        )
        with pytest.raises(InvariantError, match="address book"):
            UserPreferences(important_contacts=contacts)

    def test_too_many_topics_is_refused(self) -> None:
        topics = frozenset(
            Topic(f"topic {number}") for number in range(UserPreferences.MAX_TOPICS + 1)
        )
        with pytest.raises(InvariantError, match="topics"):
            UserPreferences(topics=topics)

    def test_one_number_cannot_appear_twice_in_the_contacts(self) -> None:
        # Which rule applies would depend on which entry is read first.
        with pytest.raises(InvariantError, match="twice"):
            UserPreferences(
                important_contacts=(
                    ImportantContact(number=NUMBER, label="Mum"),
                    ImportantContact(number=NUMBER, label="Also Mum"),
                )
            )

    def test_a_contact_is_found_by_number(self) -> None:
        preferences = UserPreferences(
            important_contacts=(ImportantContact(number=NUMBER, label="Mum"),)
        )
        found = preferences.contact_for(NUMBER)
        assert found is not None
        assert found.label == "Mum"

    def test_a_number_that_is_not_a_contact_finds_nothing(self) -> None:
        assert UserPreferences().contact_for(NUMBER) is None

    def test_caring_about_a_topic_ignores_how_it_was_typed(self) -> None:
        preferences = UserPreferences(topics=frozenset({Topic("school run")}))
        assert preferences.cares_about("School Run")
        assert not preferences.cares_about("deliveries")


def test_a_topic_cannot_carry_a_line_break() -> None:
    # Splitting on whitespace collapses every kind of it, so a multi-line value cannot be
    # smuggled into a list the model reads as one item per line.
    assert Topic("school\nrun\there").name == "school run here"


class TestDisclosableFacts:
    def test_a_fact_keeps_its_case(self) -> None:
        # Unlike a topic, which is matched against and so normalised. A fact is read out, and
        # "Tuesdays" should not become "tuesdays".
        assert DisclosableFact("Works from home on Tuesdays").text == (
            "Works from home on Tuesdays"
        )

    def test_a_fact_is_collapsed_to_one_line(self) -> None:
        # An instruction needs room, and a single line does not give it any.
        assert DisclosableFact("works\nfrom   home").text == "works from home"

    @pytest.mark.parametrize("raw", ["", "   ", "\n"])
    def test_a_fact_with_nothing_in_it_is_refused(self, raw: str) -> None:
        with pytest.raises(InvariantError):
            DisclosableFact(raw)

    def test_a_paragraph_is_refused(self) -> None:
        # This text goes in front of the model while an unknown caller is talking to it.
        with pytest.raises(InvariantError, match="instruction"):
            DisclosableFact("x" * (DisclosableFact.MAX_LENGTH + 1))

    def test_a_fact_at_the_limit_is_accepted(self) -> None:
        assert len(DisclosableFact("x" * DisclosableFact.MAX_LENGTH).text) == 120

    def test_too_many_facts_are_refused(self) -> None:
        # Every one of them is something a stranger can be told, so the list being short is the
        # point rather than a limitation.
        facts = frozenset(
            DisclosableFact(f"fact {number}") for number in range(UserPreferences.MAX_FACTS + 1)
        )
        with pytest.raises(InvariantError, match="stranger"):
            UserPreferences(disclosable_facts=facts)

    def test_nothing_is_disclosable_by_default(self) -> None:
        # The safe answer to "where are they?" is not a location.
        assert UserPreferences().disclosable_facts == frozenset()

    def test_a_fact_rendered_is_its_text(self) -> None:
        assert str(DisclosableFact("works from home")) == "works from home"
