"""Identifiers, each a distinct type so that two of them cannot be passed in swapped."""

from __future__ import annotations

from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError


@dataclass(frozen=True, slots=True)
class _Identifier:
    """A non-empty value with no surrounding space, shared by every identifier."""

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
    """Identifies one event by the identifier its provider assigned, so a repeat is visible."""
