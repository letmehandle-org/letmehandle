"""What the model is told, and what it is not.

Two kinds of test here. The first kind asserts that a phone number never reaches the context,
because that is the failure with a consequence outside the process. The second asserts the
shape byte for byte, because a field that quietly changes order changes every model response
and no test that only checks membership would notice.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from letmehandle.application.preferences.context import (
    DEFAULT_LOCALE,
    PHRASEBOOKS,
    CapabilityStatement,
    ContactStatement,
    Phrasebook,
    PreferenceContext,
    _every_phrasebook_is_complete,
    build_preference_context,
    normalise_locale,
    phrasebook_for,
)
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
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)

LONDON = "Europe/London"

# Reserved for documentation and never routable, which is the only kind of number that belongs
# in a fixture.
PARTNER_NUMBER = PhoneNumber("+12025550143")
SCHOOL_NUMBER = PhoneNumber("+12025550187")

MIDDAY = datetime(2026, 6, 1, 12, 0, tzinfo=ZoneInfo(LONDON))
MIDNIGHT = datetime(2026, 6, 1, 0, 30, tzinfo=ZoneInfo(LONDON))


def fully_populated() -> UserPreferences:
    """A preference set with something in every field, so nothing is untested by being empty."""
    return UserPreferences(
        rules=CallRules(
            default_posture=HandlingPosture.HANDLE_WITH_AGENT,
            posture_by_category={
                CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT,
                CallerCategory.KNOWN_CONTACT: HandlingPosture.PASS_THROUGH,
            },
            blocked_categories=frozenset({CallerCategory.SPAM, CallerCategory.SALES}),
            anonymous_posture=HandlingPosture.REJECT,
            active_hours=TimeWindow(time(7, 0), time(22, 0), LONDON),
            escalate_at_or_above=CallImportance.URGENT,
        ),
        authority=AgentAuthority.granting(
            Capability.TAKE_A_MESSAGE,
            Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY,
        ),
        formality=Formality.WARM,
        verbosity=Verbosity.BRIEF,
        locale="en-GB",
        important_contacts=(
            ImportantContact(PARTNER_NUMBER, "partner", HandlingPosture.PASS_THROUGH),
            ImportantContact(SCHOOL_NUMBER, "the school", HandlingPosture.HANDLE_WITH_AGENT),
        ),
        topics=frozenset({Topic("School Run"), Topic("boiler repair")}),
        disclosable_facts=frozenset(
            {DisclosableFact("is travelling this week"), DisclosableFact("prefers email")}
        ),
    )


class TestNormalisingTheLocale:
    def test_the_spellings_of_one_locale_collapse_to_one(self) -> None:
        assert normalise_locale(" EN_gb ") == "en-gb"
        assert normalise_locale("en-GB") == "en-gb"

    def test_the_normalised_locale_is_what_reaches_the_context(self) -> None:
        context = build_preference_context(UserPreferences(locale="EN_GB"), now=MIDDAY)
        assert context.locale == "en-gb"


class TestChoosingAPhrasebook:
    def test_an_exact_locale_wins(self) -> None:
        assert phrasebook_for("en") is PHRASEBOOKS[DEFAULT_LOCALE]

    def test_a_region_falls_back_to_its_language(self) -> None:
        assert phrasebook_for("en-GB") is PHRASEBOOKS[DEFAULT_LOCALE]

    def test_an_unwritten_language_falls_back_to_the_default(self) -> None:
        assert phrasebook_for("fr-CA") is PHRASEBOOKS[DEFAULT_LOCALE]

    def test_the_users_language_survives_the_fallback(self) -> None:
        # Which language to speak and which phrasebook happens to exist are different
        # questions. Answering the first with the second answers a French user in English.
        context = build_preference_context(UserPreferences(locale="fr"), now=MIDDAY)
        assert context.locale == "fr"

    def test_every_phrasebook_can_phrase_everything(self) -> None:
        # A phrasebook missing an entry fails at build time, during a call, on the one user
        # whose locale reached it.
        for phrasebook in PHRASEBOOKS.values():
            assert set(phrasebook.tone) == set(Formality)
            assert set(phrasebook.length) == set(Verbosity)
            assert set(phrasebook.capability) == set(Capability)


class TestTheInstant:
    def test_a_naive_instant_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            build_preference_context(fully_populated(), now=datetime(2026, 6, 1, 12, 0))

    def test_active_hours_are_resolved_not_handed_over(self) -> None:
        # The model is told whether the assistant is in its hours now, never the window. A window
        # is arithmetic, and arithmetic in a prompt is a coin toss about whether somebody is woken.
        preferences = fully_populated()
        assert build_preference_context(preferences, now=MIDDAY).in_active_hours
        assert not build_preference_context(preferences, now=MIDNIGHT).in_active_hours

    def test_the_zone_the_instant_arrives_in_does_not_change_the_answer(self) -> None:
        preferences = fully_populated()
        in_london = build_preference_context(preferences, now=MIDNIGHT)
        in_utc = build_preference_context(preferences, now=MIDNIGHT.astimezone(UTC))
        assert in_london == in_utc

    def test_no_window_means_the_assistant_is_always_active(self) -> None:
        # Hours nobody set are around the clock (D-027), including in the middle of the night.
        context = build_preference_context(UserPreferences(), now=MIDNIGHT)
        assert context.in_active_hours


class TestWhatIsNotDisclosed:
    def test_no_phone_number_reaches_the_context(self) -> None:
        # The one assertion in this file with a consequence outside the process. A number in
        # model context is a number a caller can ask for.
        context = build_preference_context(fully_populated(), now=MIDDAY)
        rendered = repr(context)
        assert PARTNER_NUMBER.value not in rendered
        assert SCHOOL_NUMBER.value not in rendered
        assert "2025550" not in rendered

    def test_a_contact_contributes_its_label_and_posture_and_nothing_else(self) -> None:
        context = build_preference_context(fully_populated(), now=MIDDAY)
        assert context.important_contacts == (
            ContactStatement("partner", HandlingPosture.PASS_THROUGH),
            ContactStatement("the school", HandlingPosture.HANDLE_WITH_AGENT),
        )


class TestCapabilities:
    def test_every_capability_is_stated_including_the_refused_ones(self) -> None:
        # A model told only what it may do infers the rest from silence, and silence is the
        # input a caller gets to shape.
        context = build_preference_context(fully_populated(), now=MIDDAY)
        assert {statement.capability for statement in context.capabilities} == set(Capability)

    def test_granted_and_refused_are_marked_correctly(self) -> None:
        context = build_preference_context(fully_populated(), now=MIDDAY)
        granted = {s.capability for s in context.capabilities if s.granted}
        assert granted == {
            Capability.TAKE_A_MESSAGE,
            Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY,
        }
        assert context.granted_capabilities == tuple(sorted(granted))

    def test_each_statement_carries_phrasing_from_the_phrasebook(self) -> None:
        context = build_preference_context(fully_populated(), now=MIDDAY)
        phrasebook = PHRASEBOOKS[DEFAULT_LOCALE]
        for statement in context.capabilities:
            assert statement.description == phrasebook.capability[statement.capability]

    def test_an_unconfigured_user_is_granted_nothing(self) -> None:
        context = build_preference_context(UserPreferences(), now=MIDDAY)
        assert context.granted_capabilities == ()
        assert not any(statement.granted for statement in context.capabilities)


class TestDeterminism:
    def test_sets_arrive_sorted_rather_than_in_hash_order(self) -> None:
        # A frozenset iterates in hash order, which differs between processes. The same call
        # would then produce a different context, and so a different model response, with
        # nothing in a log to say why.
        context = build_preference_context(fully_populated(), now=MIDDAY)
        assert context.topics == ("boiler repair", "school run")
        assert context.disclosable_facts == ("is travelling this week", "prefers email")
        assert context.blocked_categories == (CallerCategory.SALES, CallerCategory.SPAM)
        assert list(context.capabilities) == sorted(
            context.capabilities, key=lambda statement: statement.capability
        )

    def test_the_order_a_mapping_was_written_in_does_not_leak(self) -> None:
        rules_one = CallRules(
            posture_by_category={
                CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT,
                CallerCategory.KNOWN_CONTACT: HandlingPosture.PASS_THROUGH,
            }
        )
        rules_two = CallRules(
            posture_by_category={
                CallerCategory.KNOWN_CONTACT: HandlingPosture.PASS_THROUGH,
                CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT,
            }
        )
        one = build_preference_context(UserPreferences(rules=rules_one), now=MIDDAY)
        two = build_preference_context(UserPreferences(rules=rules_two), now=MIDDAY)
        assert one.posture_by_category == two.posture_by_category

    def test_building_twice_gives_the_same_thing(self) -> None:
        preferences = fully_populated()
        assert build_preference_context(preferences, now=MIDDAY) == build_preference_context(
            preferences, now=MIDDAY
        )


class TestVersion:
    def test_the_context_carries_the_version_it_was_built_from(self) -> None:
        context = build_preference_context(fully_populated(), now=MIDDAY)
        assert context.preferences_version == PREFERENCES_VERSION
        assert context.is_current_version

    def test_an_older_version_is_visible_rather_than_assumed_away(self) -> None:
        # The point of carrying it: a transcript can be read against the rules that applied.
        older = UserPreferences(version=PREFERENCES_VERSION + 1)
        context = build_preference_context(older, now=MIDDAY)
        assert context.preferences_version == PREFERENCES_VERSION + 1
        assert not context.is_current_version


def test_the_whole_shape_of_the_context() -> None:
    """The snapshot. An unintended change to any field fails here rather than in a call."""
    assert build_preference_context(fully_populated(), now=MIDDAY) == PreferenceContext(
        preferences_version=PREFERENCES_VERSION,
        locale="en-gb",
        tone="warm and personable",
        length="one sentence wherever one will do",
        formality=Formality.WARM,
        verbosity=Verbosity.BRIEF,
        default_posture=HandlingPosture.HANDLE_WITH_AGENT,
        anonymous_posture=HandlingPosture.REJECT,
        posture_by_category=(
            (CallerCategory.DELIVERY, HandlingPosture.HANDLE_WITH_AGENT),
            (CallerCategory.KNOWN_CONTACT, HandlingPosture.PASS_THROUGH),
        ),
        blocked_categories=(CallerCategory.SALES, CallerCategory.SPAM),
        escalate_at_or_above=CallImportance.URGENT,
        in_active_hours=True,
        capabilities=(
            CapabilityStatement(
                Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY,
                granted=True,
                description="say whether the user is free",
            ),
            CapabilityStatement(
                Capability.CONFIRM_APPOINTMENTS,
                granted=False,
                description="confirm an appointment",
            ),
            CapabilityStatement(
                Capability.DECLINE_ON_THE_USERS_BEHALF,
                granted=False,
                description="decline something on the user's behalf",
            ),
            CapabilityStatement(
                Capability.RESCHEDULE_APPOINTMENTS,
                granted=False,
                description="move an appointment to another time",
            ),
            CapabilityStatement(
                Capability.SHARE_CONTACT_DETAILS,
                granted=False,
                description="pass on the user's contact details",
            ),
            CapabilityStatement(
                Capability.SHARE_DELIVERY_INSTRUCTIONS,
                granted=False,
                description="tell a courier where to leave a parcel",
            ),
            CapabilityStatement(
                Capability.TAKE_A_MESSAGE,
                granted=True,
                description="take a message",
            ),
        ),
        important_contacts=(
            ContactStatement("partner", HandlingPosture.PASS_THROUGH),
            ContactStatement("the school", HandlingPosture.HANDLE_WITH_AGENT),
        ),
        topics=("boiler repair", "school run"),
        disclosable_facts=("is travelling this week", "prefers email"),
    )


class TestPhrasebooksAreCompleteAtImport:
    """A missing phrase must stop the process, not a call."""

    def test_the_shipped_phrasebooks_pass_the_check(self) -> None:
        # The check runs at import, so reaching this line already proves it. Calling it again
        # is what makes the guard itself covered rather than merely executed.
        _every_phrasebook_is_complete()

    def test_a_phrasebook_missing_a_phrase_is_refused(self) -> None:
        # Without this the gap surfaces mid-call, and only for the user whose formality happens
        # to be the missing one — so it could sit unnoticed until the worst possible moment.
        incomplete = Phrasebook(
            tone={Formality.WARM: "warm"},
            length=dict(PHRASEBOOKS[DEFAULT_LOCALE].length),
            capability=dict(PHRASEBOOKS[DEFAULT_LOCALE].capability),
        )
        with (
            patch.dict(
                "letmehandle.application.preferences.context.PHRASEBOOKS",
                {"xx": incomplete},
            ),
            pytest.raises(InvariantError, match="no phrasing for"),
        ):
            _every_phrasebook_is_complete()


# A fixed, timezone-aware moment. Any instant will do; what matters is that it is the same
# one on both sides of every comparison below.
AN_INSTANT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


class TestPreferencesMateriallyChangeTheContext:
    """Nothing about the assistant's behaviour may be a constant in the code.

    The acceptance criterion for this phase is that no preference value is hard-coded anywhere.
    A builder that ignored half of what it was given would still pass every determinism and
    disclosure test above, so this drives each field separately and insists the output moves.
    """

    @pytest.mark.parametrize(
        ("one", "other"),
        [
            (
                UserPreferences(formality=Formality.WARM),
                UserPreferences(formality=Formality.FORMAL),
            ),
            (
                UserPreferences(verbosity=Verbosity.BRIEF),
                UserPreferences(verbosity=Verbosity.DETAILED),
            ),
            (UserPreferences(locale="en"), UserPreferences(locale="fr")),
        ],
    )
    def test_a_personality_field_changes_what_the_model_is_told(
        self, one: UserPreferences, other: UserPreferences
    ) -> None:
        assert build_preference_context(one, now=AN_INSTANT) != build_preference_context(
            other, now=AN_INSTANT
        )

    def test_granting_a_capability_changes_it(self) -> None:
        granted = build_preference_context(
            UserPreferences(authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE)),
            now=AN_INSTANT,
        )
        withheld = build_preference_context(UserPreferences(), now=AN_INSTANT)
        assert granted.granted_capabilities != withheld.granted_capabilities

    def test_the_routing_rules_change_it(self) -> None:
        strict = build_preference_context(
            UserPreferences(rules=CallRules(default_posture=HandlingPosture.REJECT)),
            now=AN_INSTANT,
        )
        assert (
            strict.default_posture
            is not build_preference_context(UserPreferences(), now=AN_INSTANT).default_posture
        )

    def test_topics_and_facts_change_it(self) -> None:
        with_topics = build_preference_context(
            UserPreferences(
                topics=frozenset({Topic("school run")}),
                disclosable_facts=frozenset({DisclosableFact("works from home")}),
            ),
            now=AN_INSTANT,
        )
        bare = build_preference_context(UserPreferences(), now=AN_INSTANT)

        assert with_topics.topics != bare.topics
        assert with_topics.disclosable_facts != bare.disclosable_facts

    def test_an_important_contact_changes_it(self) -> None:
        known = build_preference_context(
            UserPreferences(
                important_contacts=(
                    ImportantContact(number=PhoneNumber.parse("+12025550143"), label="Mum"),
                )
            ),
            now=AN_INSTANT,
        )
        assert known.important_contacts != ()
