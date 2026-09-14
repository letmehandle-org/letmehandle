"""One spoken conversation between a speaker and a speech session, knowing nothing of calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import DomainError
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechStarted,
    TranscriptProduced,
)
from letmehandle.observability import catalogue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.ports.audio_io import AudioSink, AudioSource
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechEvent, SpeechSession

# How long the sink took to drop what it had buffered once the caller spoke.
SINK_DISCARD: Final = catalogue.measure("speech.sink_discard_seconds")


class ConversationFailedError(DomainError):
    """The session said it could not continue; `retryable` is the session's own answer."""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(f"the conversation could not continue: {reason}")
        self.reason = reason
        self.retryable = retryable
        self.failure_kind = FailureKind.UNAVAILABLE if retryable else FailureKind.REFUSED


class ConversationEnd(StrEnum):
    """Which side ended a conversation that ended without failing."""

    SPEAKER_GONE = "speaker_gone"
    SESSION_ENDED = "session_ended"


# How every conversation ended: either side, a session failure, a cancellation, or an error.
CONVERSATION_ENDED: Final = catalogue.count(
    "speech.conversation.ended",
    outcome={*ConversationEnd, "failed", "cancelled", "error"},
    retryable={"true", "false"},
)


@dataclass(frozen=True, slots=True)
class TranscriptTurn:
    """One settled thing somebody said."""

    text: str
    speaker_is_caller: bool


class Transcript:
    """What was said, in order, in memory, owned by whoever runs the conversation."""

    def __init__(self) -> None:
        self._turns: list[TranscriptTurn] = []

    @property
    def turns(self) -> tuple[TranscriptTurn, ...]:
        return tuple(self._turns)

    def record(self, turn: TranscriptTurn) -> None:
        self._turns.append(turn)


class Conversation:
    """Carries audio both ways over a lent session until either side ends or fails."""

    def __init__(
        self,
        *,
        session: SpeechSession,
        source: AudioSource,
        sink: AudioSink,
        transcript: Transcript,
        metrics: MetricsRecorder,
        clock: Clock,
    ) -> None:
        self._session = session
        self._source = source
        self._sink = sink
        self._transcript = transcript
        self._metrics = metrics
        self._clock = clock
        self._idle = asyncio.Event()
        self._idle.set()
        self._frame_started = asyncio.Event()
        self._played_at: float | None = None

    async def quiet(self, pause: float) -> None:
        """Return once nothing has played for `pause` seconds, counted from no earlier than now."""
        since = asyncio.get_running_loop().time()
        while True:
            await self._idle.wait()
            last = since if self._played_at is None else max(since, self._played_at)
            self._frame_started.clear()
            try:
                async with asyncio.timeout_at(last + pause):
                    await self._frame_started.wait()
            except TimeoutError:
                return

    async def run(self) -> ConversationEnd:
        """Carry the conversation to its end, say which side ended it, and leave nothing running."""
        labels = {"outcome": "error"}
        try:
            end = await self._carry()
        except ConversationFailedError as failure:
            labels = {"outcome": "failed", "retryable": str(failure.retryable).lower()}
            raise
        except asyncio.CancelledError:
            labels = {"outcome": "cancelled"}
            raise
        else:
            labels = {"outcome": end.value}
            return end
        finally:
            self._metrics.increment(CONVERSATION_ENDED, labels)

    async def _carry(self) -> ConversationEnd:
        try:
            async with asyncio.TaskGroup() as group:
                listening = group.create_task(self._listen())
                speaking = group.create_task(self._speak())
                await asyncio.wait((listening, speaking), return_when=asyncio.FIRST_COMPLETED)
                listening.cancel()
                speaking.cancel()
        except ExceptionGroup as failures:
            # The first failure is raised, with the group attached as its cause.
            raise failures.exceptions[0] from failures
        # The task not cancelled is the one that ended the conversation.
        return (
            ConversationEnd.SESSION_ENDED if listening.cancelled() else ConversationEnd.SPEAKER_GONE
        )

    async def _listen(self) -> None:
        async with _closing(self._source.frames()) as frames:
            async for frame in frames:
                await self._session.send_audio(frame)

    async def _speak(self) -> None:
        async with _closing(self._session.events()) as events:
            async for event in events:
                await self._handle(event)

    async def _handle(self, event: SpeechEvent) -> None:
        match event:
            case AudioProduced(frame=frame):
                # Awaited frame by frame, so a slow sink holds the session back.
                self._idle.clear()
                self._frame_started.set()
                try:
                    await self._sink.write(frame)
                finally:
                    self._played_at = asyncio.get_running_loop().time()
                    self._idle.set()
            case SpeechStarted(by_caller=True):
                heard_at = self._clock.now()
                await self._sink.discard()
                silence = (self._clock.now() - heard_at).total_seconds()
                self._metrics.observe(SINK_DISCARD, silence)
            case TranscriptProduced(is_final=True, text=text, speaker_is_caller=by_caller):
                self._transcript.record(TranscriptTurn(text, speaker_is_caller=by_caller))
            case SessionFailed(reason=reason, retryable=retryable):
                raise ConversationFailedError(reason, retryable=retryable)
            case _:
                # The model starting or stopping, and partial recognition, which is never acted on.
                pass


@asynccontextmanager
async def _closing[T](iterator: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    """Close a generator left part-way through before the conversation returns."""
    try:
        yield iterator
    finally:
        if isinstance(iterator, AsyncGenerator):
            await iterator.aclose()
