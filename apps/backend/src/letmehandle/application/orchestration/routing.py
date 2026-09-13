"""What a call's plan lets the user's rules become: the user, the assistant, or nowhere.

What the rules ask for is the domain's routing policy, `domain.policy.routing.route`, which the
handset mirrors (D-031). What a plan allows is a fact about the transport, and changes for a
different reason, so it is decided here, by `route_on`, and nowhere else.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import HandlingPosture
from letmehandle.domain.ports.call_transport import ScreeningDecision

if TYPE_CHECKING:
    from letmehandle.application.orchestration.plan import CallPlan


class Route(StrEnum):
    """What routing decided for one call."""

    # S105 reads the name as a password. It is where a call goes.
    PASS_THROUGH = "pass_through"  # noqa: S105
    ASSISTANT = "assistant"
    REJECT = "reject"


# What each posture falls back to when the plan cannot carry it out, in order of preference. A call
# the user wanted handled is better rung through than refused; one they wanted rung through is
# better handled than refused; refusing is always possible, because every transport can let a call
# go.
_FALLBACKS = {
    Route.ASSISTANT: (Route.ASSISTANT, Route.PASS_THROUGH, Route.REJECT),
    Route.PASS_THROUGH: (Route.PASS_THROUGH, Route.ASSISTANT, Route.REJECT),
    Route.REJECT: (Route.REJECT,),
}

_WANTED = {
    HandlingPosture.HANDLE_WITH_AGENT: Route.ASSISTANT,
    HandlingPosture.PASS_THROUGH: Route.PASS_THROUGH,
    HandlingPosture.REJECT: Route.REJECT,
}


def route_on(posture: HandlingPosture, plan: CallPlan) -> Route:
    """What `posture` becomes on a call with this plan.

    A call the handset already screened goes where the handset sent it: refused, or ringing — a
    silenced call still rings, without sound, and the user may answer it.
    """
    if plan.screened is not None:
        return Route.REJECT if plan.screened is ScreeningDecision.REJECT else Route.PASS_THROUGH
    available = {
        Route.ASSISTANT: plan.assistant is not None,
        Route.PASS_THROUGH: plan.put_through is not None,
        Route.REJECT: True,
    }
    return next(each for each in _FALLBACKS[_WANTED[posture]] if available[each])
