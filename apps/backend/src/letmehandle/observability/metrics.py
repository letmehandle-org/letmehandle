"""Metrics, written as structured log lines until there is somewhere better to send them.

The observability work replaces this with a real exporter. Until then a log line is the one
channel every environment already collects, and a baseline recorded there is a baseline that
exists, which is more than one waiting for a metrics backend does.

The port says labels are dimensions and never content. Here that is enforced rather than
hoped for, because the call site that puts a caller's words into a label will not look like
it: it will look like `reason=failure.reason`. So a label key must be one of a short, named
list, and a label value must look like a dimension — a lower-case token of bounded length. A
sentence, a phone number or an identifier fails both tests, and fails them loudly, in the test
that first exercises the call site, rather than quietly in a dashboard kept for a year.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Final

from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every dimension a metric may be broken down by. Adding one is a deliberate edit to this
# line, which is the review moment at which somebody asks whether its values are bounded.
LABEL_KEYS: Final = frozenset({"kind", "outcome", "provider", "retryable", "stage"})

MAX_LABEL_VALUE_LENGTH: Final = 32

# A letter first, so that neither a number nor anything shaped like one is a dimension.
_LABEL_VALUE: Final = re.compile(rf"[a-z][a-z0-9_]{{0,{MAX_LABEL_VALUE_LENGTH - 1}}}")
_METRIC_NAME: Final = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")


class MetricLabelError(ValueError):
    """A metric was given a name or a label that could carry content.

    Raised rather than stripped. A label quietly dropped is a breakdown that silently stops
    existing, and the call site that caused it is never told.
    """


def checked_labels(name: str, labels: Mapping[str, str] | None) -> dict[str, str]:
    """The labels, once they are known to be dimensions, or `MetricLabelError`."""
    if not _METRIC_NAME.fullmatch(name):
        raise MetricLabelError(f"{name!r} is not a metric name: dotted lower-case words only")
    checked = dict(labels or {})
    for key, value in checked.items():
        if key not in LABEL_KEYS:
            raise MetricLabelError(
                f"{key!r} is not a known dimension; the dimensions are {sorted(LABEL_KEYS)}"
            )
        if not _LABEL_VALUE.fullmatch(value):
            # The value is deliberately left out of the message. If it is content, repeating
            # it in an exception is the disclosure this check exists to stop.
            raise MetricLabelError(
                f"the value of {key!r} is not a dimension: a lower-case token of at most "
                f"{MAX_LABEL_VALUE_LENGTH} characters, starting with a letter"
            )
    return checked


class LoggingMetricsRecorder(MetricsRecorder):
    """Records each measurement as one structured log event."""

    def __init__(self) -> None:
        self._logger = get_logger("letmehandle.metrics")

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        checked = checked_labels(name, labels)
        if not math.isfinite(value):
            # A latency of infinity is a bug at the call site, and a JSON renderer writes it as a
            # token no log pipeline parses, so the line would be lost along with the evidence.
            raise MetricLabelError(f"{name} was observed as {value}, which is not a measurement")
        self._logger.info("metric.observed", metric=name, value=value, labels=checked)

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        self._logger.info("metric.counted", metric=name, labels=checked_labels(name, labels))
