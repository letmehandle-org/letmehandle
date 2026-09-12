"""Identifiers, each distinct from the others.

`UserId` and `CallId` are both strings underneath, and a plain `str` for both means a function
taking two of them can be called with the arguments swapped and nothing will notice until
production. Distinct types make that a type error.

They are not generated here. Generation is a port (`IdGenerator`), so that a test can make
identifiers predictable rather than discovering them from the output.
"""

from __future__ import annotations

from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError


@dataclass(frozen=True, slots=True)
class _Identifier:
    """Shared behaviour for identifiers. Not used directly."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or self.value.strip() != self.value:
            raise InvariantError(
                f"{type(self).__name__} must be a non-empty value with no surrounding space"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class UserId(_Identifier):
    """Identifies a person who has an account."""


@dataclass(frozen=True, slots=True)
class CallId(_Identifier):
    """Identifies one call, for its whole life, across every provider involved."""


@dataclass(frozen=True, slots=True)
class EventId(_Identifier):
    """Identifies one event from a provider.

    The basis of idempotency: providers redeliver, and the only reliable way to recognise a
    repeat is the identifier the provider itself assigned.
    """
