"""Metrics are dimensions. Anything that could be somebody's words or number is refused."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest
import structlog
from structlog.testing import capture_logs

from letmehandle.observability.metrics import (
    MAX_LABEL_VALUE_LENGTH,
    LoggingMetricsRecorder,
    MetricLabelError,
)
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _unfiltered_logging() -> Iterator[None]:
    # Another test may have configured logging for the process at a level that drops these
    # events before they could be captured. Whatever was configured is put back afterwards.
    configured = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**configured)


def test_an_observation_is_one_structured_event() -> None:
    with capture_logs() as events:
        LoggingMetricsRecorder().observe(
            "speech.interruption_to_silence_seconds", 0.12, {"provider": "echo"}
        )

    assert events == [
        {
            "event": "metric.observed",
            "log_level": "info",
            "metric": "speech.interruption_to_silence_seconds",
            "value": 0.12,
            "labels": {"provider": "echo"},
        }
    ]


def test_a_count_is_one_structured_event() -> None:
    with capture_logs() as events:
        LoggingMetricsRecorder().increment("speech.conversation.ended", {"outcome": "failed"})
        LoggingMetricsRecorder().increment("speech.reconnections")

    assert events == [
        {
            "event": "metric.counted",
            "log_level": "info",
            "metric": "speech.conversation.ended",
            "labels": {"outcome": "failed"},
        },
        {
            "event": "metric.counted",
            "log_level": "info",
            "metric": "speech.reconnections",
            "labels": {},
        },
    ]


CONTENT = [
    pytest.param({"transcript": "hello"}, id="a key that is not a dimension"),
    pytest.param({"outcome": "the caller asked for them"}, id="a sentence"),
    pytest.param({"outcome": "+12015550123"}, id="a phone number"),
    pytest.param({"outcome": "12015550123"}, id="bare digits"),
    pytest.param({"outcome": "Failed"}, id="free-form capitalisation"),
    pytest.param({"outcome": ""}, id="nothing"),
    pytest.param({"outcome": "a" * (MAX_LABEL_VALUE_LENGTH + 1)}, id="too long to be a dimension"),
]


@pytest.mark.parametrize("labels", CONTENT)
@pytest.mark.parametrize("recorder", [LoggingMetricsRecorder, RecordingMetrics])
def test_labels_that_could_carry_content_are_refused_and_nothing_is_emitted(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics], labels: dict[str, str]
) -> None:
    with capture_logs() as events:
        with pytest.raises(MetricLabelError):
            recorder().increment("speech.conversation.ended", labels)
        with pytest.raises(MetricLabelError):
            recorder().observe("speech.round_trip_seconds", 1.0, labels)

    assert events == []


def test_a_refused_value_is_not_repeated_in_the_error() -> None:
    # The error is logged by whoever catches it, so echoing the value would put the content
    # exactly where the check was meant to keep it out.
    with pytest.raises(MetricLabelError) as refused:
        LoggingMetricsRecorder().increment("speech.ended", {"outcome": "call me back on 0155"})

    assert "0155" not in str(refused.value)


def test_the_longest_dimension_allowed_is_accepted() -> None:
    recorder = RecordingMetrics()
    longest = "a" * MAX_LABEL_VALUE_LENGTH

    recorder.increment("speech.ended", {"outcome": longest})

    assert recorder.counted("speech.ended", outcome=longest) == 1


@pytest.mark.parametrize("name", ["Speech.Ended", "speech ended", "speech..ended", "", "9lives"])
def test_a_name_that_is_not_a_metric_name_is_refused(name: str) -> None:
    with pytest.raises(MetricLabelError):
        LoggingMetricsRecorder().increment(name)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_value_that_is_not_a_measurement_is_refused(value: float) -> None:
    with capture_logs() as events, pytest.raises(MetricLabelError):
        LoggingMetricsRecorder().observe("speech.round_trip_seconds", value)

    assert events == []
