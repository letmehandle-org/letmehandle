"""Where somebody's calls are carried from: the telephony region their number belongs to.

A deployment can serve people in more than one country, and a call is cheapest, quickest to
connect and least surprising to the person answering when it is carried by a number in their own
country. So a region is the unit a deployment's telephony is arranged by, and a user's region is
read from the number they signed in with — the one thing about them the product always knows.

A region is keyed by its country calling code, which a number states itself. Code 1 is a shared
numbering plan rather than one country, and it is named for the country this product serves in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber


@dataclass(frozen=True, slots=True)
class TelephonyRegion:
    """A region calls are arranged by: a short name, and the calling code its numbers carry."""

    name: str
    calling_code: str


US: Final = TelephonyRegion(name="US", calling_code="1")
IN: Final = TelephonyRegion(name="IN", calling_code="91")

# Every region a deployment can serve. Adding one is an entry here; nothing else names a region.
REGIONS: Final = (US, IN)

_BY_NAME: Final = {region.name: region for region in REGIONS}
_BY_CALLING_CODE: Final = {region.calling_code: region for region in REGIONS}


def region_named(name: str) -> TelephonyRegion:
    """The region with this name, in any case, or a refusal listing the names there are."""
    region = _BY_NAME.get(name.strip().upper())
    if region is None:
        raise InvariantError(
            f"no telephony region is called {name!r}; the regions are {', '.join(_BY_NAME)}"
        )
    return region


def region_of(number: PhoneNumber) -> TelephonyRegion | None:
    """The region a number belongs to, or None for a country no region covers."""
    return _BY_CALLING_CODE.get(number.calling_code)
