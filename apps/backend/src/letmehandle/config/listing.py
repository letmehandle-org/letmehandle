"""Reading the comma-separated lists configuration is written as."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def entries(text: str, separator: str = ",") -> list[str]:
    """The non-blank entries of `text`, each stripped, in order."""
    return [entry.strip() for entry in text.split(separator) if entry.strip()]


def repeated(values: Iterable[str]) -> list[str]:
    """Every value that appears more than once, sorted."""
    return sorted(value for value, count in Counter(values).items() if count > 1)


def is_calling_code(text: str) -> bool:
    """Whether `text` is a country calling code: one to three digits, not starting with zero."""
    return text.isdigit() and 1 <= len(text) <= 3 and not text.startswith("0")
