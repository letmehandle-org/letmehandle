"""A telephone number, in one form.

The product's identity is a phone number, and a number that is stored in two formats is two
numbers as far as any comparison is concerned. Normalisation happens once, here, at the edge
where a number enters the domain; nothing downstream compares raw input.

E.164 is the only form: a plus sign, a country code, and up to fifteen digits in total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError

# E.164 allows at most fifteen digits including the country code, and a country code never
# starts with zero. Punctuation is stripped before this is applied, so it describes the stored
# form rather than what a person may type.
_E164 = re.compile(r"\+[1-9][0-9]{1,14}")

# The two-digit country calling codes ITU-T E.164 assigns. Zones 1 and 7 are one digit; anything
# not listed here is three.
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
        """Normalise what a person or a provider supplied.

        Accepts the decoration people type — spaces, hyphens, brackets, dots — and the `00`
        international prefix used in much of the world. Rejects anything else rather than
        guessing: a number this cannot parse is one a human should look at, and silently
        accepting a malformed one means calls that never arrive.
        """
        if not isinstance(raw, str):  # pragma: no cover - defensive, the type says otherwise
            raise InvariantError("a phone number must be text")

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
        """The country calling code, without the plus: "1", "44", "971".

        Read from E.164's own zone structure rather than a table of countries: codes in zones 1 and
        7 are one digit, a fixed set are two, and every other code is three. The structure has not
        changed since it was assigned, so this cannot fall out of date the way a country list does.
        """
        digits = self.value[1:]
        if digits[0] in "17":
            return digits[0]
        if digits[:2] in _TWO_DIGIT_CODES:
            return digits[:2]
        return digits[:3]

    @property
    def masked(self) -> str:
        """The number with its subscriber digits hidden.

        For anything a person other than the owner might read — a log line, a metric label, an
        error. The last two digits are kept because they are what someone uses to recognise
        their own number, and two digits identify nobody.
        """
        return f"{self.value[:3]}{'*' * (len(self.value) - 5)}{self.value[-2:]}"

    def __str__(self) -> str:
        """Masked on purpose.

        Every accidental disclosure of a number this project has to worry about arrives through
        an f-string in a log line. The readable form is available, but only by asking for
        `.value` explicitly, which is a thing a reviewer can see.
        """
        return self.masked
