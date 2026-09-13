"""Where somebody's phone sends the calls it does not take.

On a deployment whose calls arrive over a telephony account, a call reaches the assistant only
because the user's own carrier forwarded it there: unanswered, or while the line was busy. The
product cannot set that up from its side — it is a setting on the user's line — so the most it
can do is say which number to forward to, and say it the same way everywhere it is asked.

A deployment serving several countries has a number in each, and a user is told the one in their
own region: forwarding a call abroad costs the user and delays the caller (D-040).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.domain.models.region import region_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.region import TelephonyRegion


@dataclass(frozen=True, slots=True)
class CallForwarding:
    """The number a user forwards unanswered and busy calls to.

    Shared by every user of a region. It names nobody: whose call a forwarded call is, the
    carrier's forwarded-from number says (D-033).
    """

    number: PhoneNumber


@dataclass(frozen=True, slots=True)
class ForwardingNumbers:
    """Which number each region's users forward to, and the one for users of any other region.

    Empty is a deployment that needs nothing forwarded: its calls arrive on a handset, or not at
    all. A region with no number of its own and no number for `elsewhere` has nothing to forward
    to either, and a user there is not asked to.
    """

    by_region: Mapping[TelephonyRegion, PhoneNumber] = field(default_factory=dict)
    elsewhere: PhoneNumber | None = None

    def for_user(self, signed_in_with: PhoneNumber) -> CallForwarding | None:
        """Where the user who signed in with this number forwards their calls, if anywhere."""
        region = region_of(signed_in_with)
        number = self.by_region.get(region) if region is not None else None
        chosen = number or self.elsewhere
        return None if chosen is None else CallForwarding(chosen)
