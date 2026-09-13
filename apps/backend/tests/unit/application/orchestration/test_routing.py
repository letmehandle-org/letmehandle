"""Routing, as a table: what each plan lets the posture the rules asked for become."""

from __future__ import annotations

import pytest

from letmehandle.application.orchestration.plan import (
    CallPlan,
    Converse,
    DialTheUser,
    LetItRing,
)
from letmehandle.application.orchestration.routing import Route, route_on
from letmehandle.domain.models.preferences import HandlingPosture
from letmehandle.domain.ports.call_transport import ScreeningDecision
from tests.contracts.fakes import StreamingTransport
from tests.support.orchestration import an_assistance

PASS = HandlingPosture.PASS_THROUGH
AGENT = HandlingPosture.HANDLE_WITH_AGENT
REJECT = HandlingPosture.REJECT

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
