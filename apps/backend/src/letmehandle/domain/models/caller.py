"""Who is on the other end, as far as anyone knows.

Unknown is the common case, and it is modelled rather than left as an absence. A `None` name
would be checked in some places and not others; `is_known` is a question every caller has to
answer deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber


class CallerCategory(StrEnum):
    """What kind of call this appears to be.

    Deliberately coarse. These are the distinctions the user's rules act on — the difference
    between a delivery and a courier is not one anybody would set a different rule for, and a
    category nobody can act on is a category that only makes classification harder.
    """

    KNOWN_CONTACT = "known_contact"
    DELIVERY = "delivery"
    HEALTHCARE = "healthcare"
    EDUCATION = "education"
    FINANCIAL = "financial"
    SERVICE_PROVIDER = "service_provider"
    SALES = "sales"
    SPAM = "spam"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Caller:
    """The other party.

    `number` is optional because a caller can withhold it, and a withheld number is a fact the
    user's rules care about rather than an error.
    """

    number: PhoneNumber | None = None
    display_name: str | None = None
    category: CallerCategory = CallerCategory.UNKNOWN

    def __post_init__(self) -> None:
        if self.display_name is not None and not self.display_name.strip():
            raise InvariantError(
                "a caller's display name is either absent or has something in it; "
                "an empty one renders as a blank space in the interface"
            )

    @property
    def is_anonymous(self) -> bool:
        """Whether the caller withheld their number."""
        return self.number is None

    @property
    def is_known(self) -> bool:
        """Whether this is somebody the user has told us about.

        A name alone is not enough: a network can supply one for a stranger. Only the
        category, which is set from the user's own contacts, makes a caller known.
        """
        return self.category is CallerCategory.KNOWN_CONTACT

    def __str__(self) -> str:
        """Safe to interpolate: a name only if the user already knows it, and a masked number.

        A caller's number and name are personal data belonging to somebody who never agreed to
        anything, so the readable form has to be asked for.
        """
        if self.display_name is not None:
            return self.display_name
        if self.number is not None:
            return self.number.masked
        return "an anonymous caller"
