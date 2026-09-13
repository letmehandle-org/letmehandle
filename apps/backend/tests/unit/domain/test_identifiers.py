"""Identifiers of different kinds are distinct types."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId, EventId, UserId

IDENTIFIERS = [UserId, CallId, EventId]


@pytest.mark.parametrize("kind", IDENTIFIERS)
def test_an_identifier_carries_its_value(kind: type[UserId]) -> None:
    assert kind("abc").value == "abc"
    assert str(kind("abc")) == "abc"


@pytest.mark.parametrize("kind", IDENTIFIERS)
@pytest.mark.parametrize("value", ["", " ", " abc", "abc ", "\tabc"])
def test_empty_or_padded_values_are_rejected(kind: type[UserId], value: str) -> None:
    with pytest.raises(InvariantError):
        kind(value)


def test_identifiers_of_different_kinds_are_not_interchangeable() -> None:
    user: object = UserId("same")
    call: object = CallId("same")
    assert user != call
    assert hash(user) != hash(call) or user != call


@pytest.mark.parametrize("kind", IDENTIFIERS)
def test_identifiers_are_immutable(kind: type[UserId]) -> None:
    identifier = kind("abc")
    with pytest.raises(AttributeError):
        identifier.value = "other"  # type: ignore[misc]
