"""A telephone number, normalised once to E.164 where it enters the domain."""

from __future__ import annotations

import re
from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError

# The stored form: a plus, a country code that does not start with zero, and at most fifteen digits.
_E164 = re.compile(r"\+[1-9][0-9]{1,14}")

# The two-digit calling codes E.164 assigns; zones 1 and 7 are one digit and every other code three.
_TWO_DIGIT_CODES = frozenset(
    {
        "20",
        "27",
        "30",
        "31",
        "32",
        "33",
        "34",
        "36",
        "39",
        "40",
        "41",
        "43",
        "44",
        "45",
        "46",
        "47",
        "48",
        "49",
        "51",
        "52",
        "53",
        "54",
        "55",
        "56",
        "57",
        "58",
        "60",
        "61",
        "62",
        "63",
        "64",
        "65",
        "66",
        "81",
        "82",
        "84",
        "86",
        "90",
        "91",
        "92",
        "93",
        "94",
        "95",
        "98",
    }
)

# The fewest digits a mask hides before it keeps a number's first and last digits visible.
_MIN_HIDDEN_DIGITS = 4

# Everything people put in a phone number that is not part of it.
_DECORATION = re.compile(r"[\s\-().]")


@dataclass(frozen=True, slots=True)
class PhoneNumber:
    """A number in E.164 form. Construct it with `parse`, not with raw input."""

    value: str

    def __post_init__(self) -> None:
        if not _E164.fullmatch(self.value):
            raise InvariantError("that is not a phone number in E.164 form, such as +12025550143")

    @classmethod
    def parse(cls, raw: str) -> PhoneNumber:
        """Normalise spaces, hyphens, brackets, dots and a `00` prefix, and refuse anything else."""
        candidate = _DECORATION.sub("", raw.strip())
        if candidate.startswith("00"):
            candidate = "+" + candidate[2:]
        if not candidate.startswith("+"):
            raise InvariantError(
                "that number has no country code. Numbers must be international, such as "
                "+12025550143, because a national number means nothing without knowing "
                "which country it is national to"
            )
        return cls(candidate)

    @property
    def calling_code(self) -> str:
        """The country calling code without the plus, read from E.164's zone structure."""
        digits = self.value[1:]
        if digits[0] in "17":
            return digits[0]
        if digits[:2] in _TWO_DIGIT_CODES:
            return digits[:2]
        return digits[:3]

    @property
    def masked(self) -> str:
        """The number with its subscriber digits hidden, keeping the last two when enough hide."""
        hidden = len(self.value) - 5
        if hidden < _MIN_HIDDEN_DIGITS:
            return "+" + "*" * (len(self.value) - 1)
        return f"{self.value[:3]}{'*' * hidden}{self.value[-2:]}"

    def __str__(self) -> str:
        """The masked form; the readable one is only `.value`."""
        return self.masked
