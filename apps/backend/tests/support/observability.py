"""Observability for a test: metrics and spans recorded where the test can read them."""

from __future__ import annotations

from letmehandle.application.resilience.circuit import Circuits
from letmehandle.bootstrap import Observability
from letmehandle.observability.in_process import InProcessMetrics, MetricsFanOut
from tests.support.recording_metrics import RecordingMetrics
from tests.support.recording_tracer import RecordingTracer


def recorded_observability(
    metrics: RecordingMetrics | None = None, tracer: RecordingTracer | None = None
) -> Observability:
    """Everything recorded, nothing exported, and circuits of the process's own defaults."""
    in_process = InProcessMetrics()
    recording = metrics or RecordingMetrics()
    fan_out = MetricsFanOut(recording, in_process)
    return Observability(
        metrics=fan_out,
        in_process=in_process,
        tracer=tracer or RecordingTracer(),
        circuits=Circuits(metrics=fan_out),
        close=lambda: None,
    )
