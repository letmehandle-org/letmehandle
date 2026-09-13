"""Intent and importance vary independently, which is why they are two judgements."""

from __future__ import annotations

from itertools import pairwise

from letmehandle.domain.models.intent import CallImportance, CallIntent


def test_undetermined_is_an_available_answer() -> None:
    # Early in a call there is no intent yet. A model forced to pick one will pick something,
    # and the rules downstream will act on it.
    assert CallIntent.UNDETERMINED in set(CallIntent)


def test_importance_is_ordered() -> None:
    # Rules say "escalate at or above this". An ordering kept in a lookup table beside an
    # unordered enum is an ordering that drifts away from it.
    assert CallImportance.IGNORABLE < CallImportance.LOW < CallImportance.ROUTINE
    assert CallImportance.ROUTINE < CallImportance.NOTABLE < CallImportance.URGENT


def test_levels_are_spaced_so_one_can_be_inserted_later() -> None:
    # Renumbering would silently change the meaning of every value already stored.
    values = sorted(level.value for level in CallImportance)
    assert all(later - earlier >= 10 for earlier, later in pairwise(values))


