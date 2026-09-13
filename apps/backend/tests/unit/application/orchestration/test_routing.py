"""Routing, as a table: what the rules ask for, and what each plan lets that become."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from letmehandle.application.orchestration.plan import (
    CallPlan,
    Converse,
    DialTheUser,
    LetItRing,
)
from letmehandle.application.orchestration.routing import Route, route, route_on
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    HandlingPosture,
    ImportantContact,
    UserPreferences,
)
from letmehandle.domain.ports.call_transport import ScreeningDecision
from tests.contracts.fakes import StreamingTransport
from tests.support.orchestration import an_assistance

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
CONTACT_NUMBER = PhoneNumber("+12025550102")
STRANGER_NUMBER = PhoneNumber("+12025550101")

PASS = HandlingPosture.PASS_THROUGH
AGENT = HandlingPosture.HANDLE_WITH_AGENT
REJECT = HandlingPosture.REJECT

PREFERENCES = UserPreferences(
    rules=CallRules(
        default_posture=AGENT,
        anonymous_posture=REJECT,
        posture_by_category={CallerCategory.DELIVERY: PASS},
        blocked_categories=frozenset({CallerCategory.SPAM}),
    ),
    important_contacts=(ImportantContact(CONTACT_NUMBER, "Mum", posture=AGENT),),
)


@pytest.mark.parametrize(
    ("caller", "posture"),
    [
        # A withheld number takes the anonymous posture, whatever its category.
        (Caller(category=CallerCategory.DELIVERY), REJECT),
        # An important contact takes their own posture, even from a blocked category.
        (Caller(number=CONTACT_NUMBER, category=CallerCategory.SPAM), AGENT),
        (Caller(number=STRANGER_NUMBER, category=CallerCategory.SPAM), REJECT),
        (Caller(number=STRANGER_NUMBER, category=CallerCategory.DELIVERY), PASS),
        (Caller(number=STRANGER_NUMBER, category=CallerCategory.UNKNOWN), AGENT),
    ],
)
def test_the_rules_ask_for_a_posture(caller: Caller, posture: HandlingPosture) -> None:
    assert route(caller, PREFERENCES, NOW) is posture


TRANSPORT = StreamingTransport()
EVERYTHING = CallPlan(
    put_through=DialTheUser(TRANSPORT),
    assistant=Converse(TRANSPORT, TRANSPORT, an_assistance()),
    escalation=DialTheUser(TRANSPORT),
    screened=None,
)
NO_ASSISTANT = CallPlan(
    put_through=DialTheUser(TRANSPORT), assistant=None, escalation=None, screened=None
)
NO_PUT_THROUGH = CallPlan(
    put_through=None,
    assistant=Converse(TRANSPORT, TRANSPORT, an_assistance()),
    escalation=None,
    screened=None,
)
NOTHING = CallPlan(put_through=None, assistant=None, escalation=None, screened=None)


def screened(decision: ScreeningDecision) -> CallPlan:
    return CallPlan(put_through=LetItRing(), assistant=None, escalation=None, screened=decision)


@pytest.mark.parametrize(
    ("posture", "plan", "expected"),
    [
        (AGENT, EVERYTHING, Route.ASSISTANT),
        (PASS, EVERYTHING, Route.PASS_THROUGH),
        (REJECT, EVERYTHING, Route.REJECT),
        # Wanted handled with no assistant: rung through rather than refused.
        (AGENT, NO_ASSISTANT, Route.PASS_THROUGH),
        # Wanted rung through with no way to put it through: handled rather than refused.
        (PASS, NO_PUT_THROUGH, Route.ASSISTANT),
        (AGENT, NOTHING, Route.REJECT),
        (PASS, NOTHING, Route.REJECT),
        (REJECT, NO_ASSISTANT, Route.REJECT),
        # A handset's decision stands, whatever the rules here say.
        (AGENT, screened(ScreeningDecision.REJECT), Route.REJECT),
        (REJECT, screened(ScreeningDecision.ALLOW), Route.PASS_THROUGH),
        (REJECT, screened(ScreeningDecision.SILENCE), Route.PASS_THROUGH),
    ],
)
def test_a_posture_becomes_what_the_plan_allows(
    posture: HandlingPosture, plan: CallPlan, expected: Route
) -> None:
    assert route_on(posture, plan) is expected
