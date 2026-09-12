"""Unknown is the common case, so it is a state rather than a missing value."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.phone_number import PhoneNumber

FICTIONAL = PhoneNumber.parse("+12025550143")


def test_a_caller_defaults_to_unknown_and_anonymous() -> None:
    caller = Caller()
    assert caller.is_anonymous
    assert not caller.is_known
    assert caller.category is CallerCategory.UNKNOWN


def test_a_withheld_number_is_a_fact_rather_than_an_error() -> None:
    # Callers withhold their numbers, and the user's rules have something to say about that.
    assert Caller(number=None, display_name="Reception").is_anonymous


def test_a_caller_with_a_number_is_not_anonymous() -> None:
    assert not Caller(number=FICTIONAL).is_anonymous


def test_a_name_alone_does_not_make_a_caller_known() -> None:
    # A network can supply a display name for a complete stranger. Only the category, which
    # comes from the user's own contacts, means the user knows them.
    assert not Caller(number=FICTIONAL, display_name="Anyone At All").is_known


def test_a_known_contact_is_known() -> None:
    caller = Caller(number=FICTIONAL, display_name="Mum", category=CallerCategory.KNOWN_CONTACT)
    assert caller.is_known


def test_an_empty_display_name_is_rejected() -> None:
    # It would render as a blank space where a name should be, which reads as a bug to a user
    # and as a name to the code.
    with pytest.raises(InvariantError):
        Caller(display_name="   ")


@pytest.mark.parametrize(
    ("caller", "expected"),
    [
        (Caller(), "an anonymous caller"),
        (Caller(number=FICTIONAL), FICTIONAL.masked),
        (Caller(number=FICTIONAL, display_name="Mum"), "Mum"),
    ],
)
def test_the_string_form_discloses_nothing_the_user_does_not_already_know(
    caller: Caller, expected: str
) -> None:
    assert str(caller) == expected


def test_the_string_form_never_contains_a_full_number() -> None:
    assert FICTIONAL.value not in str(Caller(number=FICTIONAL))
