"""The order a call is routed in, shared by the server and the phone (D-030).

A table rather than a story, so the Android evaluator can be checked against the same rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Final

import pytest

from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    HandlingPosture,
    ImportantContact,
    TimeWindow,
    UserPreferences,
)
from letmehandle.domain.policy.routing import route

PASS: Final = HandlingPosture.PASS_THROUGH
AGENT: Final = HandlingPosture.HANDLE_WITH_AGENT
REJECT: Final = HandlingPosture.REJECT

# Reserved for fiction, never routable.
STRANGER_NUMBER: Final = PhoneNumber("+12025550101")
PARTNER_NUMBER: Final = PhoneNumber("+12025550143")
SCHOOL_NUMBER: Final = PhoneNumber("+12025550187")

# The two lanes the app writes: contacts ring, everyone else meets the assistant, spam is refused.
LANES: Final = CallRules(
    default_posture=AGENT,
    anonymous_posture=AGENT,
    posture_by_category={CallerCategory.KNOWN_CONTACT: PASS},
    blocked_categories=frozenset({CallerCategory.SPAM}),
)
# Answering from nine to six, UTC so the instants below read plainly.
WORKING_DAY: Final = TimeWindow(time(9, 0), time(18, 0), "UTC")
NOON: Final = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
NIGHT: Final = datetime(2026, 1, 15, 23, 0, tzinfo=UTC)


def preferences(*, rules: CallRules = LANES, hours: TimeWindow | None = None) -> UserPreferences:
    return UserPreferences(
        rules=CallRules(
            default_posture=rules.default_posture,
            anonymous_posture=rules.anonymous_posture,
            posture_by_category=dict(rules.posture_by_category),
            blocked_categories=rules.blocked_categories,
            active_hours=hours,
        ),
        important_contacts=(
            ImportantContact(PARTNER_NUMBER, "Partner"),
            ImportantContact(SCHOOL_NUMBER, "School office", AGENT),
        ),
    )


def caller(number: PhoneNumber | None, category: CallerCategory) -> Caller:
    return Caller(number=number, category=category)


@pytest.mark.parametrize(
    ("who", "rules", "expected"),
    [
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.UNKNOWN), LANES, AGENT, id="a stranger"
        ),
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.KNOWN_CONTACT),
            LANES,
            PASS,
            id="somebody the transport knows as a contact rings",
        ),
        pytest.param(caller(None, CallerCategory.UNKNOWN), LANES, AGENT, id="a withheld number"),
        pytest.param(
            caller(None, CallerCategory.KNOWN_CONTACT),
            CallRules(
                anonymous_posture=REJECT, posture_by_category={CallerCategory.KNOWN_CONTACT: PASS}
            ),
            REJECT,
            id="a withheld number is anonymous whatever it was classified as",
        ),
        pytest.param(
            caller(SCHOOL_NUMBER, CallerCategory.KNOWN_CONTACT),
            LANES,
            AGENT,
            id="an important contact's own rule beats the contact lane",
        ),
        pytest.param(
            caller(PARTNER_NUMBER, CallerCategory.SPAM),
            LANES,
            PASS,
            id="an important contact beats a blocked category",
        ),
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.SPAM), LANES, REJECT, id="spam is refused"
        ),
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.SALES),
            CallRules(default_posture=REJECT),
            REJECT,
            id="the default applies to anything unspecified",
        ),
    ],
)
def test_the_order_holds_inside_the_users_hours(
    who: Caller, rules: CallRules, expected: HandlingPosture
) -> None:
    assert route(who, preferences(rules=rules, hours=WORKING_DAY), NOON) is expected


@pytest.mark.parametrize(
    ("who", "expected"),
    [
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.UNKNOWN),
            PASS,
            id="outside the hours a stranger rings the user",
        ),
        pytest.param(
            caller(STRANGER_NUMBER, CallerCategory.SPAM),
            REJECT,
            id="outside the hours spam is still refused",
        ),
        pytest.param(
            caller(PARTNER_NUMBER, CallerCategory.KNOWN_CONTACT),
            PASS,
            id="outside the hours a contact still rings",
        ),
    ],
)
def test_outside_the_hours_the_assistant_answers_nothing(
    who: Caller, expected: HandlingPosture
) -> None:
    assert route(who, preferences(hours=WORKING_DAY), NIGHT) is expected


def test_with_no_hours_the_assistant_answers_at_night() -> None:
    stranger = caller(STRANGER_NUMBER, CallerCategory.UNKNOWN)
    assert route(stranger, preferences(hours=None), NIGHT) is AGENT
