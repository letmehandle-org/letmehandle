"""What happens to a call before anybody speaks, in the order the handset mirrors (D-030, D-031)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import HandlingPosture

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.preferences import UserPreferences


def route(caller: Caller, preferences: UserPreferences, now: datetime) -> HandlingPosture:
    """The posture for this caller at this instant; outside the hours nothing meets the agent."""
    posture = _posture(caller, preferences)
    if posture is HandlingPosture.HANDLE_WITH_AGENT and not preferences.rules.is_active_at(now):
        return HandlingPosture.PASS_THROUGH
    return posture


def _posture(caller: Caller, preferences: UserPreferences) -> HandlingPosture:
    rules = preferences.rules
    if caller.number is None:
        return rules.anonymous_posture
    contact = preferences.contact_for(caller.number)
    if contact is not None:
        return contact.posture
    return rules.posture_for(caller.category)
