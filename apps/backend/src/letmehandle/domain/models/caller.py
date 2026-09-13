"""Who is on the other end of a call, as far as anyone knows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber


class CallerCategory(StrEnum):
    """The coarse kinds of call the user's rules act on."""

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
    """The other party; no number means the caller withheld it."""

    # Out of the repr, which a debugger, a log line or a failing assertion prints.
    number: PhoneNumber | None = field(default=None, repr=False)
    display_name: str | None = field(default=None, repr=False)
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
        """Whether the caller is one of the user's contacts; a display name alone never says so."""
        return self.category is CallerCategory.KNOWN_CONTACT

    def __str__(self) -> str:
        """The display name, else the masked number, else "an anonymous caller"."""
        if self.display_name is not None:
            return self.display_name
        if self.number is not None:
            return self.number.masked
        return "an anonymous caller"
