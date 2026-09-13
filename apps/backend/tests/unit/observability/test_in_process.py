"""Measurements are reported as percentiles over a bounded window, and counts as totals."""

from __future__ import annotations

import pytest

from letmehandle.adapters.speech.session_support.telemetry import (
    RECONNECTIONS,
    ROUND_TRIP,
    TIME_TO_FIRST_AUDIO,
)
from letmehandle.observability.catalogue import MetricLabelError
from letmehandle.observability.in_process import (
    CountSummary,
    InProcessMetrics,
    MeasureSummary,
    MetricsFanOut,
)
from tests.support.recording_metrics import RecordingMetrics


def test_measurements_are_reported_as_percentiles_per_series() -> None:
    metrics = InProcessMetrics()
    for millisecond in range(1, 101):
        metrics.observe(ROUND_TRIP, millisecond / 1000, {"provider": "echo"})
    metrics.observe(ROUND_TRIP, 2.0, {"provider": "other"})

    assert metrics.snapshot().measures == (
        MeasureSummary(ROUND_TRIP, {"provider": "echo"}, 100, 0.05, 0.09, 0.099, 0.1),
        MeasureSummary(ROUND_TRIP, {"provider": "other"}, 1, 2.0, 2.0, 2.0, 2.0),
    )


def test_only_the_most_recent_measurements_are_kept_but_every_one_is_counted() -> None:
    metrics = InProcessMetrics(window=3)
    for seconds in (9.0, 9.0, 9.0, 1.0, 2.0, 3.0):
        metrics.observe(TIME_TO_FIRST_AUDIO, seconds, {"provider": "echo"})

    [summary] = metrics.snapshot().measures
    assert summary.count == 6
    assert (summary.p50, summary.maximum) == (2.0, 3.0)


def test_counts_are_totals_per_series() -> None:
    metrics = InProcessMetrics()
    metrics.increment(RECONNECTIONS, {"provider": "echo", "outcome": "failed"})
    metrics.increment(RECONNECTIONS, {"outcome": "failed", "provider": "echo"})
    metrics.increment(RECONNECTIONS, {"provider": "echo", "outcome": "succeeded"})

    assert metrics.snapshot().counts == (
        CountSummary(RECONNECTIONS, {"outcome": "failed", "provider": "echo"}, 2),
        CountSummary(RECONNECTIONS, {"outcome": "succeeded", "provider": "echo"}, 1),
    )


def test_a_refused_measurement_leaves_no_trace() -> None:
    metrics = InProcessMetrics()

    with pytest.raises(MetricLabelError):
        metrics.observe(ROUND_TRIP, float("nan"), {"provider": "echo"})
    with pytest.raises(MetricLabelError):
        metrics.increment(RECONNECTIONS, {"outcome": "+12025550123"})

    assert metrics.snapshot().counts == ()
    assert metrics.snapshot().measures == ()


def test_a_fan_out_hands_every_measurement_to_each_recorder() -> None:
    first, second = RecordingMetrics(), RecordingMetrics()
    both = MetricsFanOut(first, second)

    both.observe(ROUND_TRIP, 0.3, {"provider": "echo"})
    both.increment(RECONNECTIONS, {"outcome": "failed"})

    for recorder in (first, second):
        assert recorder.observed(ROUND_TRIP) == [0.3]
        assert recorder.counted(RECONNECTIONS, outcome="failed") == 1
