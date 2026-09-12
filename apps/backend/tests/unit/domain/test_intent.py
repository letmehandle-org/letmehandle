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


def test_the_interrupt_threshold_is_stated_once() -> None:
    assert not CallImportance.ROUTINE.is_at_least_notable
    assert CallImportance.NOTABLE.is_at_least_notable
    assert CallImportance.URGENT.is_at_least_notable


def test_intent_and_importance_are_independent() -> None:
    # The reason they are separate: a sales call is unimportant whatever it is about, and a
    # delivery matters only while the courier is standing there.
    assert CallIntent.SALES.value != CallImportance.IGNORABLE.name.lower()
    assert len(set(CallIntent)) > 1
    assert len(set(CallImportance)) > 1
