"""Metrics are recorded only as declared; anything that could be words or a number is refused."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pytest
import structlog
from structlog.testing import capture_logs

from letmehandle.adapters.speech.session_support.telemetry import ROUND_TRIP
from letmehandle.application.speech.conversation import CONVERSATION_ENDED
from letmehandle.observability.catalogue import MAX_LABEL_VALUE_LENGTH, MetricLabelError
from letmehandle.observability.metrics import LoggingMetricsRecorder
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
        LoggingMetricsRecorder().observe(ROUND_TRIP, 0.12, {"provider": "echo"})

    assert events == [
        {
            "event": "metric.observed",
            "log_level": "info",
            "metric": ROUND_TRIP,
            "value": 0.12,
            "labels": {"provider": "echo"},
        }
    ]


def test_a_count_is_one_structured_event() -> None:
    with capture_logs() as events:
        LoggingMetricsRecorder().increment(CONVERSATION_ENDED, {"outcome": "failed"})
        LoggingMetricsRecorder().increment(CONVERSATION_ENDED)

    assert events == [
        {
            "event": "metric.counted",
            "log_level": "info",
            "metric": CONVERSATION_ENDED,
            "labels": {"outcome": "failed"},
        },
        {
            "event": "metric.counted",
            "log_level": "info",
            "metric": CONVERSATION_ENDED,
            "labels": {},
        },
    ]


RECORDERS = pytest.mark.parametrize("recorder", [LoggingMetricsRecorder, RecordingMetrics])

# Against a label that lists its values: anything not listed is refused, however harmless it looks.
NOT_LISTED = [
    pytest.param({"transcript": "hello"}, id="a key the metric was not declared with"),
    pytest.param({"provider": "echo"}, id="a real dimension this metric does not have"),
    pytest.param({"outcome": "the caller asked for them"}, id="a sentence"),
    pytest.param({"outcome": "+12025550123"}, id="a phone number"),
    pytest.param({"outcome": "Failed"}, id="free-form capitalisation"),
    pytest.param({"outcome": "succeeded"}, id="a token that is not one of its values"),
]

# Against a label named in code: anything not shaped like a name is refused.
NOT_A_NAME = [
    pytest.param({"provider": "the caller asked for them"}, id="a sentence"),
    pytest.param({"provider": "+12025550123"}, id="a phone number"),
    pytest.param({"provider": "12025550123"}, id="bare digits"),
    pytest.param({"provider": "Echo"}, id="free-form capitalisation"),
    pytest.param({"provider": ""}, id="nothing"),
    pytest.param({"provider": "a" * (MAX_LABEL_VALUE_LENGTH + 1)}, id="too long to be a name"),
]


@pytest.mark.parametrize("labels", NOT_LISTED)
@RECORDERS
def test_a_value_its_label_does_not_list_is_refused_and_nothing_is_emitted(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics], labels: dict[str, str]
) -> None:
    with capture_logs() as events, pytest.raises(MetricLabelError):
        recorder().increment(CONVERSATION_ENDED, labels)

    assert events == []


@pytest.mark.parametrize("labels", NOT_A_NAME)
@RECORDERS
def test_a_name_written_in_code_must_look_like_one(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics], labels: dict[str, str]
) -> None:
    with capture_logs() as events, pytest.raises(MetricLabelError):
        recorder().observe(ROUND_TRIP, 1.0, labels)

    assert events == []


@RECORDERS
def test_a_metric_nobody_declared_is_refused(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics],
) -> None:
    with pytest.raises(MetricLabelError, match="not a declared metric"):
        recorder().increment("speech.something_new")


@RECORDERS
def test_a_count_cannot_be_recorded_as_a_measurement_or_the_other_way_round(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics],
) -> None:
    with pytest.raises(MetricLabelError, match="declared as a count"):
        recorder().observe(CONVERSATION_ENDED, 1.0)
    with pytest.raises(MetricLabelError, match="declared as a measure"):
        recorder().increment(ROUND_TRIP)


def test_a_refused_value_is_not_repeated_in_the_error() -> None:
    # The error is logged by whoever catches it, so echoing the value would put the content
    # exactly where the check was meant to keep it out.
    with pytest.raises(MetricLabelError) as refused:
        LoggingMetricsRecorder().increment(CONVERSATION_ENDED, {"outcome": "call me on 0155"})

    assert "0155" not in str(refused.value)


def test_the_longest_name_allowed_is_accepted() -> None:
    recorder = RecordingMetrics()
    longest = "a" * MAX_LABEL_VALUE_LENGTH

    recorder.observe(ROUND_TRIP, 0.5, {"provider": longest})

    assert recorder.observed(ROUND_TRIP) == [0.5]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
@RECORDERS
def test_a_value_that_is_not_a_measurement_is_refused(
    recorder: type[LoggingMetricsRecorder | RecordingMetrics], value: float
) -> None:
    with capture_logs() as events, pytest.raises(MetricLabelError):
        recorder().observe(ROUND_TRIP, value)

    assert events == []
