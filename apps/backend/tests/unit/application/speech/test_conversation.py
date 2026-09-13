"""A spoken conversation, carried with no call anywhere near it.

Every test here runs the conversation against the in-memory echo session, a generated tone and
a sink that only takes notes. No call, transport or phone number exists in any of them, which is
the point: if the speech layer quietly depended on a call, these could not be written.

Resources are asserted by counting what is left. A conversation that says it stopped both
directions and leaves a task behind is the failure that takes a service down over a week.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.speech.conversation import (
    CONVERSATION_ENDED,
    SINK_DISCARD,
    Conversation,
    ConversationEnd,
    ConversationFailedError,
    Transcript,
    TranscriptTurn,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFrame
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEnded,
    SpeechStarted,
    TranscriptProduced,
)
from tests.contracts.fakes import EchoSpeechProvider, EchoSpeechSession, FixedClock
from tests.support.audio import RecordingSink, ToneSource, tone_frame
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


class Harness:
    """One conversation's parts, built fresh for each test."""

    def __init__(self, session: EchoSpeechSession, source: ToneSource, sink: RecordingSink) -> None:
        self.session = session
        self.source = source
        self.sink = sink
        self.transcript = Transcript()
        self.metrics = RecordingMetrics()
        self.clock = FixedClock()

    def conversation(self) -> Conversation:
        return Conversation(
            session=self.session,
            source=self.source,
            sink=self.sink,
            transcript=self.transcript,
            metrics=self.metrics,
            clock=self.clock,
        )


async def open_session() -> EchoSpeechSession:
    session = await EchoSpeechProvider().connect(
        system_context="a conversation with nobody in particular",
        voice_id="calm",
        greeting="Hello.",
        locale="en",
        input_format=SPEECH_WIDEBAND,
    )
    assert isinstance(session, EchoSpeechSession)
    return session


@pytest.fixture
async def session() -> AsyncIterator[EchoSpeechSession]:
    # The test owns the session, as a real caller of the conversation would. The conversation
    # is lent it and must give it back open.
    async with await open_session() as opened:
        assert isinstance(opened, EchoSpeechSession)
        yield opened


def other_tasks() -> set[asyncio.Task[object]]:
    return asyncio.all_tasks() - {asyncio.current_task()}


async def test_a_conversation_runs_with_no_call_transport_or_phone_number_present(
    session: EchoSpeechSession,
) -> None:
    parts = Harness(session, ToneSource(frames=5, stays_open=True), RecordingSink())
    running = asyncio.create_task(parts.conversation().run())

    await parts.sink.until_written(5)
    parts.source.hang_up()

    assert await running is ConversationEnd.SPEAKER_GONE
    # Echoed back frame for frame: the audio went in through the source, through the session,
    # and out through the sink, with nothing but the abstractions in between.
    assert parts.sink.written == parts.source.said()
    assert parts.sink.discarded_after == []
    assert not session.is_closed
    assert parts.metrics.counted(CONVERSATION_ENDED, outcome="speaker_gone") == 1


async def test_the_source_ending_ends_the_conversation(session: EchoSpeechSession) -> None:
    # Nothing is waiting in the session, so the player is parked on it when the speaker goes.
    # It has to be stopped rather than left listening to a session with nobody on the line.
    before = other_tasks()
    parts = Harness(session, ToneSource(frames=0), RecordingSink())

    assert await parts.conversation().run() is ConversationEnd.SPEAKER_GONE
    assert other_tasks() == before


async def test_the_session_ending_ends_the_conversation() -> None:
    before = other_tasks()
    async with await open_session() as session:
        assert isinstance(session, EchoSpeechSession)
        parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
        await session.close()

        assert await parts.conversation().run() is ConversationEnd.SESSION_ENDED
    assert other_tasks() == before
    assert parts.metrics.counted(CONVERSATION_ENDED, outcome="session_ended") == 1


async def test_the_caller_starting_to_speak_discards_what_the_sink_was_about_to_play(
    session: EchoSpeechSession,
) -> None:
    parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
    reply = tone_frame(0)
    for event in (
        AudioProduced(reply),
        AudioProduced(reply),
        SpeechStarted(by_caller=True),
        AudioProduced(reply),
    ):
        await session.emit(event)
    await session.close()

    await parts.conversation().run()

    assert parts.sink.discarded_after == [2]
    assert len(parts.sink.written) == 3
    # The conversation clears its own side only. The session's queue is the session's to
    # empty, and interrupting it from here as well would interrupt it twice.
    assert session.interruptions == 0


async def test_the_model_starting_to_speak_discards_nothing(session: EchoSpeechSession) -> None:
    parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
    await session.emit(SpeechStarted(by_caller=False))
    await session.emit(SpeechEnded(by_caller=True))
    await session.close()

    await parts.conversation().run()

    assert parts.sink.discarded_after == []


async def test_how_long_an_interruption_took_to_go_quiet_is_measured(
    session: EchoSpeechSession,
) -> None:
    class SlowToClear(RecordingSink):
        def __init__(self, clock: FixedClock) -> None:
            super().__init__()
            self._clock = clock

        async def discard(self) -> None:
            self._clock.advance(0.25)
            await super().discard()

    parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
    parts.sink = SlowToClear(parts.clock)
    await session.emit(SpeechStarted(by_caller=True))
    await session.close()

    await parts.conversation().run()

    [observed] = parts.metrics.observations
    assert (observed.name, observed.value) == (SINK_DISCARD, 0.25)
    assert not observed.labels


async def test_only_settled_transcript_turns_are_kept(session: EchoSpeechSession) -> None:
    parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
    for event in (
        TranscriptProduced("can I spe", speaker_is_caller=True, is_final=False),
        TranscriptProduced("can I speak to them", speaker_is_caller=True, is_final=True),
        TranscriptProduced("they are away today", speaker_is_caller=False, is_final=True),
    ):
        await session.emit(event)
    await session.close()

    await parts.conversation().run()

    assert parts.transcript.turns == (
        TranscriptTurn("can I speak to them", speaker_is_caller=True),
        TranscriptTurn("they are away today", speaker_is_caller=False),
    )


@pytest.mark.parametrize("retryable", [True, False])
async def test_a_failed_session_ends_the_conversation_with_a_typed_error(
    session: EchoSpeechSession, retryable: bool
) -> None:
    before = other_tasks()
    parts = Harness(session, ToneSource(frames=None), RecordingSink())
    await session.emit(TranscriptProduced("hello", speaker_is_caller=True, is_final=True))
    await session.emit(SessionFailed(reason="the stream dropped", retryable=retryable))

    with pytest.raises(ConversationFailedError) as failure:
        await parts.conversation().run()

    assert failure.value.reason == "the stream dropped"
    assert failure.value.retryable is retryable
    # What was said before the failure is still there to read, which is when it matters most.
    assert parts.transcript.turns == (TranscriptTurn("hello", speaker_is_caller=True),)
    assert other_tasks() == before
    retryable_label = str(retryable).lower()
    assert (
        parts.metrics.counted(CONVERSATION_ENDED, outcome="failed", retryable=retryable_label) == 1
    )


async def test_a_failing_sink_surfaces_its_own_error_and_leaves_nothing_running(
    session: EchoSpeechSession,
) -> None:
    class Unplugged(RecordingSink):
        async def write(self, frame: AudioFrame) -> None:
            raise ProviderError("speaker", "unplugged", retryable=False)

    before = other_tasks()
    parts = Harness(session, ToneSource(frames=None), Unplugged())

    # Unwrapped: a caller catching a provider error should not have to know that the
    # conversation happened to use a task group.
    with pytest.raises(ProviderError, match="unplugged"):
        await parts.conversation().run()

    assert other_tasks() == before
    assert parts.metrics.counted(CONVERSATION_ENDED, outcome="error") == 1


async def test_a_slow_sink_holds_the_source_back_rather_than_buffering(
    session: EchoSpeechSession,
) -> None:
    before = other_tasks()
    parts = Harness(session, ToneSource(frames=None), RecordingSink())
    parts.sink.hold()
    running = asyncio.create_task(parts.conversation().run())
    await parts.sink.held.wait()

    # The source never ends, so a conversation that did not wait for the sink would take
    # another frame on every one of these turns of the loop.
    await settle()
    stalled_at = parts.source.taken
    await settle()

    assert parts.source.taken == stalled_at
    # What the bound allows and no more: one frame's reply in the sink's hands, the echo
    # session's eight queued events holding four more, and one waiting for room.
    assert stalled_at <= 6
    assert parts.sink.written == []

    # Released, it carries on from where it stopped, and nothing was lost in the wait.
    parts.sink.release()
    await parts.sink.until_written(stalled_at)
    parts.source.hang_up()
    assert await running is ConversationEnd.SPEAKER_GONE
    assert parts.sink.written == parts.source.said()[: len(parts.sink.written)]
    assert other_tasks() == before


class TestQuiet:
    """Waiting until the session has stopped playing for a pause."""

    PAUSE = 0.05

    async def test_nothing_playing_is_quiet_after_the_pause(
        self, session: EchoSpeechSession
    ) -> None:
        parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
        conversation = parts.conversation()
        running = asyncio.create_task(conversation.run())
        loop = asyncio.get_running_loop()
        started = loop.time()

        await conversation.quiet(self.PAUSE)

        assert loop.time() - started >= self.PAUSE
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    async def test_a_frame_still_playing_is_waited_for(self, session: EchoSpeechSession) -> None:
        parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
        conversation = parts.conversation()
        parts.sink.hold()
        running = asyncio.create_task(conversation.run())
        await session.emit(AudioProduced(tone_frame(0)))
        await parts.sink.held.wait()

        quiet = asyncio.create_task(conversation.quiet(self.PAUSE))
        await asyncio.sleep(self.PAUSE * 2)
        assert not quiet.done()

        loop = asyncio.get_running_loop()
        parts.sink.release()
        released = loop.time()
        await quiet
        assert loop.time() - released >= self.PAUSE
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)

    async def test_a_frame_played_during_the_pause_starts_it_again(
        self, session: EchoSpeechSession
    ) -> None:
        parts = Harness(session, ToneSource(frames=0, stays_open=True), RecordingSink())
        conversation = parts.conversation()
        running = asyncio.create_task(conversation.run())
        loop = asyncio.get_running_loop()

        quiet = asyncio.create_task(conversation.quiet(self.PAUSE * 2))
        await asyncio.sleep(self.PAUSE)
        await session.emit(AudioProduced(tone_frame(0)))
        await parts.sink.until_written(1)
        played = loop.time()
        await quiet

        assert loop.time() - played >= self.PAUSE * 2
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class Point:
    """Somewhere a conversation can be cancelled: how to set it up, and how to know it is there."""

    source: Callable[[], ToneSource]
    held: bool
    reached: Callable[[Harness], Awaitable[None]]


async def listening_to_silence(parts: Harness) -> None:
    await parts.source.silent.wait()


async def playback_held(parts: Harness) -> None:
    await parts.session.emit(AudioProduced(tone_frame(0)))
    await parts.sink.held.wait()


async def session_full(parts: Harness) -> None:
    await parts.sink.held.wait()
    await settle()


async def settle() -> None:
    """Let everything runnable run until it blocks.

    Proving that something stops requires letting it try to continue, and there is no event
    for "nothing more happened".
    """
    for _ in range(100):
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    "point",
    [
        Point(lambda: ToneSource(frames=0, stays_open=True), False, listening_to_silence),
        Point(lambda: ToneSource(frames=0, stays_open=True), True, playback_held),
        Point(lambda: ToneSource(frames=None), True, session_full),
    ],
    ids=["waiting-for-the-model", "mid-playback", "under-backpressure"],
)
async def test_cancelling_the_conversation_leaves_no_task_alive(
    session: EchoSpeechSession, point: Point
) -> None:
    before = other_tasks()
    parts = Harness(session, point.source(), RecordingSink())
    if point.held:
        parts.sink.hold()
    running = asyncio.create_task(parts.conversation().run())
    await point.reached(parts)

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    assert other_tasks() == before
    assert not session.is_closed
    assert parts.metrics.counted(CONVERSATION_ENDED, outcome="cancelled") == 1


async def test_cancelling_before_it_starts_leaves_no_task_alive(
    session: EchoSpeechSession,
) -> None:
    before = other_tasks()
    parts = Harness(session, ToneSource(frames=None), RecordingSink())
    running = asyncio.create_task(parts.conversation().run())
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert other_tasks() == before
