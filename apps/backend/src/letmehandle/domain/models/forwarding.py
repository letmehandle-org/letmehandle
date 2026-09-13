"""The number a user's phone forwards unanswered and busy calls to, per region (D-034, D-041)."""

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
    """The number a user forwards calls to, shared by every user of a region (D-033)."""

    number: PhoneNumber


@dataclass(frozen=True, slots=True)
class ForwardingNumbers:
    """Each region's forwarding number, and the one for users of any other region; empty is none."""

    by_region: Mapping[TelephonyRegion, PhoneNumber] = field(default_factory=dict)
    elsewhere: PhoneNumber | None = None

    def for_user(self, signed_in_with: PhoneNumber) -> CallForwarding | None:
        """Where the user who signed in with this number forwards their calls, if anywhere."""
        region = region_of(signed_in_with)
        number = self.by_region.get(region) if region is not None else None
        chosen = number or self.elsewhere
        return None if chosen is None else CallForwarding(chosen)
