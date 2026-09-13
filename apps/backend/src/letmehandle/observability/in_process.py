"""Metrics kept in this process: counts since it started, and recent measurements as percentiles.

What diagnostics reads, so that somebody investigating a slow deployment can see the latency at
every provider boundary without a metrics backend. Percentiles rather than averages, because a
reply that is slow for one caller in fifty is a product problem an average hides entirely.

Measurements are kept per series in a window of the most recent, so memory is bounded by the
window times the number of series, and the number of series is bounded by the catalogue.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.catalogue import Instrument
from letmehandle.observability.metrics import checked_labels, checked_value

if TYPE_CHECKING:
    from collections.abc import Mapping

# How many recent measurements each series keeps. Enough that the 99th percentile is ten real
# measurements rather than one.
WINDOW: Final = 1_000

type Series = tuple[str, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class CountSummary:
    """How often something happened since the process started."""

    metric: str
    labels: Mapping[str, str]
    count: int


@dataclass(frozen=True, slots=True)
class MeasureSummary:
    """The spread of recent measurements of one series. `count` is every one ever taken."""

    metric: str
    labels: Mapping[str, str]
    count: int
    p50: float
    p90: float
    p99: float
    maximum: float


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Every series, at one moment, in a stable order."""

    counts: tuple[CountSummary, ...]
    measures: tuple[MeasureSummary, ...]


class InProcessMetrics(MetricsRecorder):
    """Counts and recent measurements, in memory, for this process's diagnostics."""

    def __init__(self, *, window: int = WINDOW) -> None:
        self._window = window
        self._counts: Counter[Series] = Counter()
        self._recent: defaultdict[Series, deque[float]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )
        self._taken: Counter[Series] = Counter()

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        series = _series(name, checked_labels(name, Instrument.MEASURE, labels))
        measured = checked_value(name, value)
        self._recent[series].append(measured)
        self._taken[series] += 1

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        self._counts[_series(name, checked_labels(name, Instrument.COUNT, labels))] += 1

    def snapshot(self) -> MetricsSnapshot:
        """Where every series stands now."""
        return MetricsSnapshot(
            counts=tuple(
                CountSummary(name, dict(labels), count)
                for (name, labels), count in sorted(self._counts.items())
            ),
            measures=tuple(
                _summarise(name, dict(labels), sorted(recent), self._taken[(name, labels)])
                for (name, labels), recent in sorted(self._recent.items())
            ),
        )


class MetricsFanOut(MetricsRecorder):
    """Every measurement, handed to each recorder in turn."""

    def __init__(self, *recorders: MetricsRecorder) -> None:
        self._recorders = recorders

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        for recorder in self._recorders:
            recorder.observe(name, value, labels)

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        for recorder in self._recorders:
            recorder.increment(name, labels)


def _series(name: str, labels: Mapping[str, str]) -> Series:
    return name, tuple(sorted(labels.items()))


def _summarise(
    name: str, labels: Mapping[str, str], ordered: list[float], taken: int
) -> MeasureSummary:
    return MeasureSummary(
        metric=name,
        labels=labels,
        count=taken,
        p50=_percentile(ordered, 0.50),
        p90=_percentile(ordered, 0.90),
        p99=_percentile(ordered, 0.99),
        maximum=ordered[-1],
    )


def _percentile(ordered: list[float], quantile: float) -> float:
    # Nearest rank: always a measurement that was taken, never one interpolated between two.
    return ordered[max(math.ceil(quantile * len(ordered)) - 1, 0)]
