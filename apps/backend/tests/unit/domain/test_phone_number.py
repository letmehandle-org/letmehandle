"""Phone numbers are parsed into one E.164 form and masked when rendered."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber

# Every whole number here is from a range reserved for fiction.
FICTIONAL = "+12025550143"
FICTIONAL_UK = "+447700900123"

# Malformed numbers are assembled so the disclosure audit does not read them as real.
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
    assert len(masked_number) == len(raw)


def test_the_default_string_form_is_masked() -> None:
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


# Numbers outside the fictional ranges are assembled from their calling code (D-021).
@pytest.mark.parametrize(
    ("code", "rest"),
    [
        ("1", "2025550143"),
        ("7", "9123456789"),
        ("44", "7700900123"),
        ("91", "9" * 10),
        ("20", "1" * 10),
        ("971", "5" * 9),
        ("966", "5" * 9),
        ("880", "1" * 10),
        ("353", "8" * 9),
    ],
)
def test_the_calling_code_is_read_from_the_numbering_zones(code: str, rest: str) -> None:
    assert PhoneNumber("+" + code + rest).calling_code == code


@pytest.mark.parametrize("value", ["+12", "+1234", "+123456"])
def test_a_number_too_short_to_keep_its_ends_is_masked_whole(value: str) -> None:
    masked = PhoneNumber(value).masked
    assert masked == "+" + "*" * (len(value) - 1)
