"""Metrics written as structured log lines, checked against their catalogue declarations."""

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
            # The value is left out of the message, since it may be content.
            raise MetricLabelError(
                f"the value of {key!r} on {name} is not one it was declared with: a listed value, "
                f"or for a name written in code a lower-case token of at most "
                f"{MAX_LABEL_VALUE_LENGTH} characters, starting with a letter"
            )
    return checked


def checked_value(name: str, value: float) -> float:
    """`value`, once it is known to be a measurement, or `MetricLabelError`."""
    if not math.isfinite(value):
        # Non-finite values are refused, since a JSON renderer cannot write them parseably.
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
