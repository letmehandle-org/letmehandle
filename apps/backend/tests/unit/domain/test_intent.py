"""Call intent, and importance as an ordered scale."""

from __future__ import annotations

from itertools import pairwise

from letmehandle.domain.models.intent import CallImportance, CallIntent


def test_undetermined_is_an_available_answer() -> None:
    assert CallIntent.UNDETERMINED in set(CallIntent)


def test_importance_is_ordered() -> None:
    assert CallImportance.IGNORABLE < CallImportance.LOW < CallImportance.ROUTINE
    assert CallImportance.ROUTINE < CallImportance.NOTABLE < CallImportance.URGENT


def test_levels_are_spaced_so_one_can_be_inserted_later() -> None:
    values = sorted(level.value for level in CallImportance)
    assert all(later - earlier >= 10 for earlier, later in pairwise(values))
