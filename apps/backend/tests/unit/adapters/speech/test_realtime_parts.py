"""The small pieces a session is built from: backoff, remembered context, and measurement."""

from __future__ import annotations

import pytest

from letmehandle.adapters.speech.realtime import telemetry
from letmehandle.adapters.speech.realtime.context import SessionContext
from letmehandle.adapters.speech.realtime.protocol import Speaker, Turn
from letmehandle.adapters.speech.realtime.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.realtime.telemetry import SessionTelemetry, StreamErrorKind
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


# ----------------------------------------------------------------------------------- context


def make_context(history_turns: int = 2) -> SessionContext:
    return SessionContext(
        instructions="first",
        voice_id="calm",
        language="en",
        transcription_model=None,
        history_turns=history_turns,
    )


def test_restoration_is_configuration_then_turns_oldest_first() -> None:
    context = make_context()
    context.instructions = "second"
    context.remember(Turn(Speaker.CALLER, "one"))
    context.remember(Turn(Speaker.ASSISTANT, "two"))

    configuration, *turns = context.restoration()

    assert configuration["session"]["instructions"] == "second"
    assert [turn["item"]["content"][0]["text"] for turn in turns] == ["one", "two"]


def test_history_forgets_the_oldest_turn_past_its_bound() -> None:
    context = make_context(history_turns=2)
    for text in ("one", "two", "three"):
        context.remember(Turn(Speaker.CALLER, text))
    texts = [turn["item"]["content"][0]["text"] for turn in context.restoration()[1:]]
    assert texts == ["two", "three"]


def test_forgetting_a_turn_leaves_an_identical_earlier_one() -> None:
    # The assistant may well say "one moment" twice; only the one that was not heard goes.
    context = make_context(history_turns=3)
    earlier, later = Turn(Speaker.ASSISTANT, "one moment"), Turn(Speaker.ASSISTANT, "one moment")
    context.remember(earlier)
    context.remember(Turn(Speaker.CALLER, "ok"))
    context.remember(later)
    context.forget(later)
    texts = [turn["item"]["content"][0]["text"] for turn in context.restoration()[1:]]
    assert texts == ["one moment", "ok"]


def test_a_negative_history_bound_is_refused() -> None:
    with pytest.raises(InvariantError):
        make_context(history_turns=-1)


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
