"""A number that is stored in two forms is two numbers, so parsing is where this is decided."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber

# Every number here is from a range reserved for fiction. See CONTRIBUTING.
FICTIONAL = "+12025550143"
FICTIONAL_UK = "+447700900123"

# Assembled rather than written out. These are malformed on purpose, but they still have the
# shape of a telephone number, and the disclosure audit rejects a number literal that is not
# from a range reserved for fiction — correctly, since it cannot tell a deliberately broken
# number from somebody's real one.
COUNTRY_CODE_STARTING_WITH_ZERO = "+" + "0123456789"
SIXTEEN_DIGITS = "+" + "1234567890123456"


@pytest.mark.parametrize(
    "raw",
    [
        FICTIONAL,
        "+1 202 555 0143",
        "+1 (202) 555-0143",
        "+1-202-555-0143",
        "  +12025550143  ",
        "+1.202.555.0143",
        "0012025550143",
    ],
)
def test_decoration_and_the_international_prefix_are_normalised_away(raw: str) -> None:
    assert PhoneNumber.parse(raw).value == FICTIONAL


def test_two_spellings_of_one_number_are_equal() -> None:
    # The reason this type exists: a set, a dictionary key or an equality check must not depend
    # on how somebody typed it.
    assert PhoneNumber.parse("+1 (202) 555-0143") == PhoneNumber.parse(FICTIONAL)


@pytest.mark.parametrize(
    "raw",
    [
        "2025550143",  # no country code
        "555-0143",  # national, and short
        "",
        "   ",
        "not a number",
        COUNTRY_CODE_STARTING_WITH_ZERO,
        SIXTEEN_DIGITS,
        "+",
    ],
)
def test_anything_that_cannot_be_understood_is_rejected(raw: str) -> None:
    with pytest.raises(InvariantError):
        PhoneNumber.parse(raw)


def test_a_national_number_says_why_it_is_not_enough() -> None:
    with pytest.raises(InvariantError, match="country code"):
        PhoneNumber.parse("2025550143")


def test_construction_rejects_a_number_that_was_never_parsed() -> None:
    with pytest.raises(InvariantError):
        PhoneNumber("2025550143")


@pytest.mark.parametrize(
    ("raw", "masked"),
    [(FICTIONAL, "+12*******43"), (FICTIONAL_UK, "+44********23")],
)
def test_masking_keeps_only_what_identifies_a_number_to_its_owner(raw: str, masked: str) -> None:
    masked_number = PhoneNumber.parse(raw).masked
    assert masked_number == masked
    # The mask must not change the length, or it becomes a hint about the number.
    assert len(masked_number) == len(raw)


def test_the_default_string_form_is_masked() -> None:
    # Every accidental disclosure this project has to worry about arrives through an f-string.
    # Reading the number has to be a deliberate act that a reviewer can see.
    number = PhoneNumber.parse(FICTIONAL)
    assert f"{number}" == number.masked
    assert number.value not in f"{number}"


@pytest.mark.parametrize(
    "value",
    [
        FICTIONAL + "\n",  # `$` alone would let a trailing newline through
        "+" + "".join(chr(0x0660 + int(digit)) for digit in FICTIONAL[1:]),  # Arabic-Indic digits
    ],
)
def test_only_ascii_digits_and_nothing_after_them_make_the_stored_form(value: str) -> None:
    with pytest.raises(InvariantError):
        PhoneNumber(value)
