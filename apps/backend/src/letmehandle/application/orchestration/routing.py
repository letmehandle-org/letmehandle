"""Where a call goes before anybody speaks to it: to the user, to the assistant, or nowhere.

Two pure functions, and the only place routing is decided. `route` reads the user's rules and says
what they ask for; `route_on` says what the call's plan lets that become. Kept apart because they
change for different reasons: the rules are the user's, while what a plan allows is a fact about the
transport.

`route` has the signature of the domain's routing policy, which takes over the rules — the user's
active hours among them — and replaces it here in one change. Until then it applies the rules that
need no clock, and no hours are read anywhere in orchestration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import HandlingPosture
from letmehandle.domain.ports.call_transport import ScreeningDecision

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.application.orchestration.plan import CallPlan
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.preferences import UserPreferences


class Route(StrEnum):
    """What routing decided for one call."""

    # S105 reads the name as a password. It is where a call goes.
    PASS_THROUGH = "pass_through"  # noqa: S105
    ASSISTANT = "assistant"
    REJECT = "reject"


def route(caller: Caller, preferences: UserPreferences, now: datetime) -> HandlingPosture:
    """What the user's rules ask for this caller, in the order the handset applies them too.

    A withheld number takes the anonymous posture; an important contact, their own; a blocked
    category is rejected; anybody else takes their category's posture or the default. `now` is
    unread here, and in the signature because the domain policy that replaces this reads the
    user's hours.
    """
    rules = preferences.rules
    if caller.number is None:
        return rules.anonymous_posture
    contact = preferences.contact_for(caller.number)
    if contact is not None:
        return contact.posture
    return rules.posture_for(caller.category)


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
