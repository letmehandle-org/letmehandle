"""One spoken conversation, carried between a speaker and a speech session.

Two directions run at once: what the speaker says goes into the session, and what the session
produces comes out to the speaker. Neither may outlive the other. A listener left running after
the speaker has gone keeps a session busy with nobody on the other end; a player left running
after the session has failed plays nothing, forever, and holds a task nobody will cancel.

This module knows nothing of calls, transports, numbers or providers, and that is the property
it exists to demonstrate. A phone call, a microphone and an in-memory test tone are the same
source to it.

It does not convert audio. The speech adapter converts at its own edge, in both directions, and
a second conversion here would be a second place to disagree about a sample rate.
"""

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

# How long the sink took to drop what it had buffered once the caller spoke. Its own name, not
# the session's interruption-to-silence: that one runs from cancelling the model to the model
# going quiet, and one name for two measurements averages them into a number that is neither.
SINK_DISCARD: Final = catalogue.measure("speech.sink_discard_seconds")


class ConversationFailedError(DomainError):
    """The session said it could not continue, so neither can the conversation.

    Its own type rather than `ProviderError`, because this layer does not know which provider
    it is talking to and a provider name made up here would be a fact that is not true.
    `retryable` is carried over unchanged: whether to open another session is the owner's
    decision, and it needs the session's own answer to make it.
    """

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(f"the conversation could not continue: {reason}")
        self.reason = reason
        self.retryable = retryable
        self.failure_kind = FailureKind.UNAVAILABLE if retryable else FailureKind.REFUSED


class ConversationEnd(StrEnum):
    """Which side ended a conversation that ended without failing."""

    SPEAKER_GONE = "speaker_gone"
    SESSION_ENDED = "session_ended"


# How every conversation ended: one of the two ways above, a failure the session reported, a
# cancellation, or an error of anything else.
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
    """What was said, in order, in memory only.

    Owned by whoever runs the conversation rather than returned by it, so that it survives the
    conversation failing or being cancelled. Those are the conversations somebody most needs to
    read afterwards. Nothing here writes it anywhere; keeping transcripts is a later decision
    with its own retention rules.
    """

    def __init__(self) -> None:
        self._turns: list[TranscriptTurn] = []

    @property
    def turns(self) -> tuple[TranscriptTurn, ...]:
        return tuple(self._turns)

    def record(self, turn: TranscriptTurn) -> None:
        self._turns.append(turn)


class Conversation:
    """Carries audio both ways until the speaker goes, the session ends, or either fails.

    The session must already be open, and it is not closed here. Whoever opened it owns it: they
    may run another conversation over it, or tell it something first, and a use case that closed
    what it was lent would make both impossible and would close it twice in the common case.

    Interruption is handled on this side of the session as well as inside it. When the caller
    starts speaking, audio already handed to the sink is discarded, because a model that stops
    producing while the speaker plays out its buffer still talks over the person who interrupted.
    Emptying the session's own queue is the session's job and is left to it.
    """

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

    async def run(self) -> ConversationEnd:
        """Carry the conversation to its end.

        Returns which side ended it. Raises `ConversationFailedError` when the session fails,
        and anything the source, sink or session raise on their own, unwrapped. On every exit,
        cancellation included, both directions have stopped before this returns.
        """
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
            # A task group reports failures as a group, which no caller catching the error it
            # expects would recognise. The first is what ended the conversation; the group stays
            # attached as the cause, so a second failure during teardown is not lost.
            raise failures.exceptions[0] from failures
        # Cancelling a task that has already finished does nothing, so the one that was not
        # cancelled is the one that ended the conversation.
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
                # Awaited, one frame at a time. A sink playing slowly therefore slows this loop,
                # which stops draining the session's bounded queue, which is how backpressure
                # reaches the model instead of piling up in memory here.
                await self._sink.write(frame)
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
                # The model starting or stopping, and partial recognition. Partials are left
                # out on purpose: acting on one is how an assistant answers a question the
                # caller had not finished asking.
                pass


@asynccontextmanager
async def _closing[T](iterator: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    """Close a generator left part-way through, before the conversation returns.

    Leaving a loop early — on a failure, say — leaves the generator suspended, and the event
    loop finalises a suspended generator by starting a task of its own, later. That is a task
    alive after the conversation claimed to have stopped everything. An iterator that is not a
    generator has nothing to finalise.
    """
    try:
        yield iterator
    finally:
        if isinstance(iterator, AsyncGenerator):
            await iterator.aclose()
