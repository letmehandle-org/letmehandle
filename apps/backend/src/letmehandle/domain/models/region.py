"""Telephony regions, keyed by country calling code, and the region a number belongs to (D-041)."""

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


# Calling code 1 is a shared numbering plan, named for the country this product serves in it.
US: Final = TelephonyRegion(name="US", calling_code="1")
IN: Final = TelephonyRegion(name="IN", calling_code="91")

# Every region a deployment can serve.
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
