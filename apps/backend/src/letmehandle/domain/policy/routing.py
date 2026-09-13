"""What happens to a call before anybody has spoken to it, decided without a model.

One order, written once, because it is evaluated in two places: on the server for a transport that
sees the call first there, and on a phone that screens the call before it rings. The phone
mirrors this function line for line (D-028), so a rule that reads differently in the two is a user
whose calls are handled one way on one path and another way on the other.

1. **A withheld number** has no number to recognise, so the user's rule for anonymous calls applies.
2. **An important contact** is somebody the user named, and their own rule wins over any category.
3. **A blocked category** is rejected. A category cannot be both blocked and given a posture.
4. **The category's posture**, or the default. Whether a caller is `known_contact` is the
   transport's to say: the phone's own address book on the device, only important contacts on the
   server. The address book never leaves the phone.

Then the user's hours (D-027). Outside them the assistant answers nothing and the call rings the
user as if there were no assistant. What was rejected stays rejected — the assistant being off is
not a reason to let spam through — and what already rang still rings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import HandlingPosture

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.preferences import UserPreferences


def route(caller: Caller, preferences: UserPreferences, now: datetime) -> HandlingPosture:
    """The posture for this caller at this instant. Pure: the same input, the same answer."""
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
