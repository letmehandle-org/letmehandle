"""The person the assistant represents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.preferences import UserPreferences

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber


@dataclass(frozen=True, slots=True)
class User:
    """An account.

    The phone number is the identity, not a detail on it: it is what a caller dials and what
    the assistant answers for. A user without one is not a user this product can serve.

    `display_name` is what the assistant calls them to a caller. Optional, because a user who
    would rather the assistant not give their name to strangers is expressing a preference
    rather than leaving a field blank.
    """

    id: UserId
    phone_number: PhoneNumber
    display_name: str | None = None
    preferences: UserPreferences = field(default_factory=UserPreferences)

    def __post_init__(self) -> None:
        if self.display_name is not None and not self.display_name.strip():
            raise InvariantError(
                "a display name is either absent or has something in it; the assistant would "
                "otherwise introduce the user as nobody"
            )

    def __str__(self) -> str:
        """The identifier alone.

        Neither the name nor the number: this appears in logs, and both are personal data.
        """
        return f"user {self.id.value}"
