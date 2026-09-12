"""A metrics recorder that remembers, for asserting what a use case measured."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.metrics import checked_labels

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class Observation:
    """One recorded measurement."""

    name: str
    value: float
    labels: tuple[tuple[str, str], ...]


def _key(labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(labels.items()))


class InMemoryMetricsRecorder(MetricsRecorder):
    """Keeps everything it is given, and refuses the same labels the real recorder refuses.

    Checking here too is what makes a use case's tests catch a content label. A recorder that
    accepted anything would let the mistake through to the first environment using the real one.
    """

    def __init__(self) -> None:
        self.observations: list[Observation] = []
        self._counts: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self.observations.append(Observation(name, value, _key(checked_labels(name, labels))))

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        self._counts[(name, _key(checked_labels(name, labels)))] += 1

    def count(self, name: str, **labels: str) -> int:
        """How many times `name` was counted with exactly these labels."""
        return self._counts[(name, _key(labels))]
