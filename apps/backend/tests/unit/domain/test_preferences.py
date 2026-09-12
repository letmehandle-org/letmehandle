"""Timezones are where this kind of code goes quietly wrong, so they are where the tests are."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.preferences import (
    CallRules,
    Formality,
    HandlingPosture,
    TimeWindow,
    UserPreferences,
)

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
