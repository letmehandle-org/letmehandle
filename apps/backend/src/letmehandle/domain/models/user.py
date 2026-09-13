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
    """An account, identified by its phone number, with the name the assistant may give callers."""

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
        """The identifier alone, never the name or the number."""
        return f"user {self.id.value}"
