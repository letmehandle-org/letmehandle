"""The pieces every speech session is built from: backoff, remembered turns, and measurement."""

from __future__ import annotations

import pytest

from letmehandle.adapters.speech.session_support import telemetry
from letmehandle.adapters.speech.session_support.history import (
    ConversationHistory,
    Speaker,
    Turn,
)
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry, StreamErrorKind
from letmehandle.domain.errors import InvariantError
from tests.support.recording_metrics import RecordingMetrics

# ----------------------------------------------------------------------------------- backoff


def test_backoff_grows_exponentially_until_its_ceiling() -> None:
    policy = ReconnectPolicy(max_attempts=6, initial_delay_seconds=0.5, max_delay_seconds=3.0)
    ceilings = [policy.delay(attempt, draw=0.999_999) for attempt in range(6)]
    assert ceilings == pytest.approx([0.5, 1.0, 2.0, 3.0, 3.0, 3.0], rel=1e-5)


@pytest.mark.parametrize("attempt", range(5))
def test_jitter_stays_within_half_and_all_of_the_ceiling(attempt: int) -> None:
    # Never zero, so failures cannot become a tight loop; never over, so a caller is not left
    # waiting longer than the policy promises.
    policy = ReconnectPolicy(initial_delay_seconds=0.25, max_delay_seconds=4.0)
    ceiling = min(4.0, 0.25 * 2**attempt)
    assert policy.delay(attempt, draw=0.0) == pytest.approx(ceiling / 2)
    assert policy.delay(attempt, draw=0.5) == pytest.approx(ceiling * 0.75)
    assert ceiling / 2 < policy.delay(attempt, draw=0.3) < ceiling


@pytest.mark.parametrize(
    ("attempts", "initial", "ceiling"), [(0, 0.1, 1.0), (1, 0.0, 1.0), (1, 2.0, 1.0)]
)
def test_a_policy_that_would_never_or_always_wait_is_refused(
    attempts: int, initial: float, ceiling: float
) -> None:
    with pytest.raises(InvariantError):
        ReconnectPolicy(attempts, initial, ceiling)


# ----------------------------------------------------------------------------------- history


def test_a_replaced_turn_keeps_its_place() -> None:
    # A correction says what was actually heard; it belongs where the original was said.
    history = ConversationHistory(3)
    first, cut = Turn(Speaker.CALLER, "hello"), Turn(Speaker.ASSISTANT, "let me tell you all")
    history.remember(first)
    history.remember(cut)
    history.remember(Turn(Speaker.CALLER, "stop"))
    history.replace(cut, Turn(Speaker.ASSISTANT, "let me"))
    assert [turn.text for turn in history] == ["hello", "let me", "stop"]


def test_replacing_a_turn_already_forgotten_changes_nothing() -> None:
    history = ConversationHistory(1)
    old = Turn(Speaker.CALLER, "old")
    history.remember(old)
    history.remember(Turn(Speaker.CALLER, "new"))
    history.replace(old, Turn(Speaker.CALLER, "rewritten"))
    assert [turn.text for turn in history] == ["new"]


# --------------------------------------------------------------------------------- telemetry


class ManualClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def metrics() -> RecordingMetrics:
    return RecordingMetrics()


@pytest.fixture
def measured(metrics: RecordingMetrics, clock: ManualClock) -> SessionTelemetry:
    return SessionTelemetry(metrics, clock, "realtime")


def test_time_to_first_audio_is_measured_once(
    measured: SessionTelemetry, metrics: RecordingMetrics, clock: ManualClock
) -> None:
    measured.opened()
    clock.now += 0.4
    measured.model_audio_arrived()
    clock.now += 1
    measured.model_audio_arrived()
    assert metrics.observed(telemetry.TIME_TO_FIRST_AUDIO) == pytest.approx([0.4])


def test_each_utterance_has_its_own_round_trip(
    measured: SessionTelemetry, metrics: RecordingMetrics, clock: ManualClock
) -> None:
    for wait in (0.3, 0.7):
        measured.caller_stopped()
        clock.now += wait
        measured.model_audio_arrived()
        measured.model_audio_arrived()
    assert metrics.observed(telemetry.ROUND_TRIP) == pytest.approx([0.3, 0.7])


def test_interruption_to_silence_runs_from_the_first_interruption(
    measured: SessionTelemetry, metrics: RecordingMetrics, clock: ManualClock
) -> None:
    measured.silenced()
    measured.interrupted()
    clock.now += 0.1
    measured.interrupted()
    clock.now += 0.1
    measured.silenced()
    measured.silenced()
    assert metrics.observed(telemetry.INTERRUPTION_TO_SILENCE) == pytest.approx([0.2])


def test_a_reconnection_is_counted_and_timed_by_outcome(
    measured: SessionTelemetry, metrics: RecordingMetrics, clock: ManualClock
) -> None:
    measured.caller_stopped()
    measured.reconnecting()
    clock.now += 1.5
    measured.reconnected(succeeded=True)
    measured.model_audio_arrived()

    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 1
    assert metrics.observed(telemetry.RECONNECTION_DURATION) == pytest.approx([1.5])
    # An utterance the old connection never answered is not a round trip of five seconds.
    assert metrics.observed(telemetry.ROUND_TRIP) == []


def test_labels_are_dimensions_only(measured: SessionTelemetry, metrics: RecordingMetrics) -> None:
    measured.stream_error(StreamErrorKind.MALFORMED)
    measured.reconnecting()
    measured.reconnected(succeeded=False)
    assert {key for labels in metrics.all_labels() for key in labels} <= {
        "provider",
        "kind",
        "outcome",
    }
