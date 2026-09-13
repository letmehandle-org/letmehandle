"""What a call's plan lets the user's routing posture become (D-031)."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import HandlingPosture
from letmehandle.domain.ports.call_transport import ScreeningDecision

if TYPE_CHECKING:
    from letmehandle.application.orchestration.plan import CallPlan


class Route(StrEnum):
    """What routing decided for one call."""

    PASS_THROUGH = "pass_through"  # noqa: S105
    ASSISTANT = "assistant"
    REJECT = "reject"


# What each posture falls back to when the plan cannot carry it out, in order.
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
    """What `posture` becomes on this plan; a screened call goes where its handset sent it."""
    if plan.screened is not None:
        return Route.REJECT if plan.screened is ScreeningDecision.REJECT else Route.PASS_THROUGH
    available = {
        Route.ASSISTANT: plan.assistant is not None,
        Route.PASS_THROUGH: plan.put_through is not None,
        Route.REJECT: True,
    }
    return next(each for each in _FALLBACKS[_WANTED[posture]] if available[each])
