"""A metrics recorder that keeps what it is given, so a test can read it back.

It refuses the same labels the real recorder refuses. A recorder that accepted anything is how
a label the production recorder rejects — and raises on — reached a stream-error path with
every test still passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.metrics import checked_labels

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class Recorded:
    """One observation or count."""

    name: str
    value: float
    labels: Mapping[str, str]


@dataclass
class RecordingMetrics(MetricsRecorder):
    """Every observation and count, in order."""

    observations: list[Recorded] = field(default_factory=list)
    counts: list[Recorded] = field(default_factory=list)

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        self.observations.append(Recorded(name, value, checked_labels(name, labels)))

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        self.counts.append(Recorded(name, 1, checked_labels(name, labels)))

    def observed(self, name: str) -> list[float]:
        return [each.value for each in self.observations if each.name == name]

    def counted(self, name: str, **labels: str) -> int:
        return sum(
            1
            for each in self.counts
            if each.name == name and all(each.labels.get(k) == v for k, v in labels.items())
        )

    def all_labels(self) -> list[Mapping[str, str]]:
        return [each.labels for each in (*self.observations, *self.counts)]
