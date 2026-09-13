"""What a speech session measures about itself.

Latency is the product here — a reply that arrives a second late is a caller talking over it —
and a regression in it is invisible without a baseline, so these are recorded from the first
session rather than added when somebody complains.

Every label is a dimension: which provider, which kind of failure. Nothing said in a call and no
identifier of a call or a person is ever passed in, and nothing here accepts one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.observability import catalogue

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.timing import MonotonicClock
    from letmehandle.domain.ports.metrics import MetricsRecorder


class StreamErrorKind(StrEnum):
    """Why a stream error was counted."""

    CONNECTION = "connection"
    SERVICE = "service"
    MALFORMED = "malformed"


TIME_TO_FIRST_AUDIO: Final = catalogue.measure(
    "speech.time_to_first_audio_seconds", provider=catalogue.NAMED_IN_CODE
)
ROUND_TRIP: Final = catalogue.measure("speech.round_trip_seconds", provider=catalogue.NAMED_IN_CODE)
INTERRUPTION_TO_SILENCE: Final = catalogue.measure(
    "speech.interruption_to_silence_seconds", provider=catalogue.NAMED_IN_CODE
)
_RECONNECTION_OUTCOMES: Final = frozenset({"succeeded", "failed"})
RECONNECTIONS: Final = catalogue.count(
    "speech.reconnections", provider=catalogue.NAMED_IN_CODE, outcome=_RECONNECTION_OUTCOMES
)
RECONNECTION_DURATION: Final = catalogue.measure(
    "speech.reconnection_seconds", provider=catalogue.NAMED_IN_CODE, outcome=_RECONNECTION_OUTCOMES
)
STREAM_ERRORS: Final = catalogue.count(
    "speech.stream_errors", provider=catalogue.NAMED_IN_CODE, kind=StreamErrorKind
)


class SessionTelemetry:
    """The measurements of one session, taken against an injected clock."""

    def __init__(self, recorder: MetricsRecorder, clock: MonotonicClock, provider: str) -> None:
        self._recorder = recorder
        self._clock = clock
        self._labels = {"provider": provider}
        self._opened_at: float | None = None
        self._caller_stopped_at: float | None = None
        self._interrupted_at: float | None = None
        self._reconnecting_since = 0.0

    def opened(self) -> None:
        """The session is ready. The origin of time to first audio."""
        self._opened_at = self._clock()

    def caller_stopped(self) -> None:
        """The caller finished an utterance. The origin of its round trip."""
        self._caller_stopped_at = self._clock()

    def model_audio_arrived(self) -> None:
        """Audio from the model. Closes whichever measurements were waiting for it."""
        now = self._clock()
        if self._opened_at is not None:
            self._recorder.observe(TIME_TO_FIRST_AUDIO, now - self._opened_at, self._labels)
            self._opened_at = None
        if self._caller_stopped_at is not None:
            self._recorder.observe(ROUND_TRIP, now - self._caller_stopped_at, self._labels)
            self._caller_stopped_at = None

    def interrupted(self) -> None:
        """The model was told to stop. A second interruption before silence keeps the first."""
        if self._interrupted_at is None:
            self._interrupted_at = self._clock()

    def silenced(self) -> None:
        """The service confirmed the interrupted response is over."""
        if self._interrupted_at is not None:
            elapsed = self._clock() - self._interrupted_at
            self._recorder.observe(INTERRUPTION_TO_SILENCE, elapsed, self._labels)
            self._interrupted_at = None

    def reconnecting(self) -> None:
        """A connection dropped and recovery began."""
        self._reconnecting_since = self._clock()
        self._caller_stopped_at = None
        self._interrupted_at = None

    def reconnected(self, *, succeeded: bool) -> None:
        """Recovery ended, one way or the other. Always follows `reconnecting`."""
        outcome = "succeeded" if succeeded else "failed"
        labels = {**self._labels, "outcome": outcome}
        self._recorder.increment(RECONNECTIONS, labels)
        elapsed = self._clock() - self._reconnecting_since
        self._recorder.observe(RECONNECTION_DURATION, elapsed, labels)

    def stream_error(self, kind: StreamErrorKind) -> None:
        """Something went wrong on the stream, whether or not the session survived it."""
        self._recorder.increment(STREAM_ERRORS, {**self._labels, "kind": kind})
