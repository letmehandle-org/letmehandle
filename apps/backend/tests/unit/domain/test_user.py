"""The phone number is the identity, not a detail hanging off it."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.user import User

NUMBER = PhoneNumber.parse("+12025550143")


def test_a_user_carries_defaults_that_grant_nothing() -> None:
    user = User(id=UserId("u1"), phone_number=NUMBER)
    assert user.preferences == UserPreferences()
    assert user.preferences.authority == AgentAuthority.none()


def test_a_user_may_choose_not_to_have_their_name_given_out() -> None:
    assert User(id=UserId("u1"), phone_number=NUMBER).display_name is None


def test_a_blank_display_name_is_refused() -> None:
    # The assistant would otherwise introduce the user as nobody.
    with pytest.raises(InvariantError):
        User(id=UserId("u1"), phone_number=NUMBER, display_name="  ")


def test_the_string_form_gives_away_neither_the_name_nor_the_number() -> None:
    user = User(id=UserId("u1"), phone_number=NUMBER, display_name="Alex")
    rendered = str(user)
    assert "u1" in rendered
    assert "Alex" not in rendered
    assert NUMBER.value not in rendered


def test_preferences_travel_with_the_user() -> None:
    user = User(
        id=UserId("u1"),
        phone_number=NUMBER,
        preferences=UserPreferences(authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE)),
    )
    assert user.preferences.authority.allows(Capability.TAKE_A_MESSAGE)
