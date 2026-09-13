"""Metrics, written as structured log lines, and checked against what was declared.

A log line is the one channel every environment already collects, and a baseline recorded there is
a baseline that exists. `InProcessMetrics` beside it keeps the same measurements as percentiles for
diagnostics; either can be joined by an exporter without touching a call site.

The port says labels are dimensions and never content. Here that is enforced rather than hoped
for, because the call site that puts a caller's words into a label will not look like it: it will
look like `reason=failure.reason`. So every metric is declared with the values its labels may take
(see `catalogue.py`), and a recording that strays from its declaration fails loudly, in the test
that first exercises the call site, rather than quietly in a dashboard kept for a year.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.catalogue import (
    LABEL_VALUE,
    MAX_LABEL_VALUE_LENGTH,
    Instrument,
    MetricLabelError,
    NamedInCode,
    spec_for,
)
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping


def checked_labels(
    name: str, instrument: Instrument, labels: Mapping[str, str] | None
) -> dict[str, str]:
    """The labels, once known to be what `name` was declared with, or `MetricLabelError`."""
    spec = spec_for(name, instrument)
    checked = dict(labels or {})
    for key, value in checked.items():
        allowed = spec.labels.get(key)
        if allowed is None:
            raise MetricLabelError(f"{name} has no {key!r} label; it has {sorted(spec.labels)}")
        known = (
            LABEL_VALUE.fullmatch(value) is not None
            if isinstance(allowed, NamedInCode)
            else value in allowed
        )
        if not known:
            # The value is deliberately left out of the message. If it is content, repeating
            # it in an exception is the disclosure this check exists to stop.
            raise MetricLabelError(
                f"the value of {key!r} on {name} is not one it was declared with: a listed value, "
                f"or for a name written in code a lower-case token of at most "
                f"{MAX_LABEL_VALUE_LENGTH} characters, starting with a letter"
            )
    return checked


def checked_value(name: str, value: float) -> float:
    """`value`, once it is known to be a measurement, or `MetricLabelError`."""
    if not math.isfinite(value):
        # A latency of infinity is a bug at the call site, and a JSON renderer writes it as a
        # token no log pipeline parses, so the line would be lost along with the evidence.
        raise MetricLabelError(f"{name} was observed as {value}, which is not a measurement")
    return value


class LoggingMetricsRecorder(MetricsRecorder):
    """Records each measurement as one structured log event."""

    def __init__(self) -> None:
        self._logger = get_logger("letmehandle.metrics")

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        checked = checked_labels(name, Instrument.MEASURE, labels)
        self._logger.info(
            "metric.observed", metric=name, value=checked_value(name, value), labels=checked
        )

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        checked = checked_labels(name, Instrument.COUNT, labels)
        self._logger.info("metric.counted", metric=name, labels=checked)
