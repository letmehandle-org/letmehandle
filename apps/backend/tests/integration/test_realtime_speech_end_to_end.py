"""A spoken conversation, end to end, with no account and no call.

Everything here is the real code a deployment runs — the settings, the bootstrap, the realtime
session, the websocket connection and the conversation use case — talking over a real socket to
a simulated service on loopback. The only stand-ins are the service itself and the speaker and
speaker-box at either end, which is the plan's condition for proving the speech layer works
before any endpoint or transport exists.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

import pytest

from letmehandle.adapters.clock import SystemClock
from letmehandle.adapters.speech.session_support.telemetry import RECONNECTIONS, TIME_TO_FIRST_AUDIO
from letmehandle.application.speech.conversation import (
    Conversation,
    ConversationEnd,
    ConversationFailedError,
    Transcript,
)
from letmehandle.bootstrap import build_speech_provider
from letmehandle.config.settings import ConfigurationError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.ports.audio_io import AudioSource
from tests.support.audio import RecordingSink, tone_frame
from tests.support.config import EXAMPLE_DEFAULT_VOICE, make_settings
from tests.support.recording_metrics import RecordingMetrics
from tests.support.simulated_realtime_service import (
    SIMULATED_API_KEY,
    SIMULATED_MODEL,
    SimulatedRealtimeService,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from letmehandle.domain.ports.speech import SpeechProvider, SpeechSession

# How long a turn hums before falling silent. The simulation hears any non-silent audio as speech
# and a silent frame after it as the end of the turn, which is all a turn needs to be.
TURN_FRAMES: Final = 10
SILENCE: Final = AudioFrame(b"\x00" * len(tone_frame(0).data), SPEECH_WIDEBAND)
# Long enough for a response over loopback, short enough that a hang fails the test promptly.
PATIENCE_SECONDS: Final = 10.0
LATEST_CONTEXT: Final = "The person being called has joined; keep the caller on the line."


class Speaker(AudioSource):
    """Somebody who says what the test tells them to, then stays quiet on the line."""

    def __init__(self) -> None:
        self._frames: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    def say_something(self) -> None:
        for index in range(TURN_FRAMES):
            self._frames.put_nowait(tone_frame(index))
        self._frames.put_nowait(SILENCE)

    def hang_up(self) -> None:
        self._frames.put_nowait(None)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        while (frame := await self._frames.get()) is not None:
            yield frame


@pytest.fixture
async def service() -> AsyncIterator[SimulatedRealtimeService]:
    async with SimulatedRealtimeService() as running:
        yield running


def provider_for(service: SimulatedRealtimeService, metrics: RecordingMetrics) -> SpeechProvider:
    settings = make_settings(
        speech_endpoint_url=service.url,
        speech_model=SIMULATED_MODEL,
        speech_api_key=SIMULATED_API_KEY,
    )
    return build_speech_provider(settings, metrics=metrics)


async def connect(provider: SpeechProvider) -> SpeechSession:
    return await provider.connect(
        system_context="You answer calls for somebody who is busy.",
        voice_id=EXAMPLE_DEFAULT_VOICE,
        greeting="Hello.",
        locale="en",
        input_format=SPEECH_WIDEBAND,
    )


class Call:
    """One conversation running in the background, and the parts a test looks at."""

    def __init__(self, session: SpeechSession, metrics: RecordingMetrics) -> None:
        self.speaker = Speaker()
        self.sink = RecordingSink()
        self.transcript = Transcript()
        self.task = asyncio.create_task(
            Conversation(
                session=session,
                source=self.speaker,
                sink=self.sink,
                transcript=self.transcript,
                metrics=metrics,
                clock=SystemClock(),
            ).run()
        )

    async def until_heard(self, frames: int) -> None:
        await asyncio.wait_for(self.sink.until_written(frames), PATIENCE_SECONDS)


async def test_a_conversation_is_held_end_to_end_over_a_real_socket(
    service: SimulatedRealtimeService,
) -> None:
    metrics = RecordingMetrics()
    before = len(asyncio.all_tasks())

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say_something()
        await call.until_heard(1)

        call.speaker.hang_up()
        assert await asyncio.wait_for(call.task, PATIENCE_SECONDS) is ConversationEnd.SPEAKER_GONE

    await service.wait_until_idle()
    # The model answered in audio, the handshake carried the key, and nothing outlived the call.
    assert call.sink.written
    assert service.handshakes[0].authorization == f"Bearer {SIMULATED_API_KEY}"
    assert metrics.observed(TIME_TO_FIRST_AUDIO)
    assert service.open_connections == 0
    assert len(asyncio.all_tasks()) == before


async def test_a_caller_talking_over_the_model_silences_it(
    service: SimulatedRealtimeService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        # Hold the reply mid-sentence, once some of it has been heard, then speak over it: the
        # only way to interrupt a response deterministically rather than hoping one is playing.
        service.hold_responses(after_deltas=1)
        call.speaker.say_something()
        await call.until_heard(1)
        call.speaker.say_something()

        await asyncio.wait_for(_until(lambda: bool(call.sink.discarded_after)), PATIENCE_SECONDS)
        service.release_responses()
        call.speaker.hang_up()
        await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    await service.wait_until_idle()
    assert call.sink.discarded_after, "what the speaker had buffered must be dropped"
    # And the model was stopped, and told how much was heard, by the session: the speaker going
    # quiet alone leaves the model talking and believing it was heard to the end.
    after_the_caller_spoke = service.received[0].since_speech_started(1)
    assert "response.cancel" in after_the_caller_spoke
    assert "conversation.item.truncate" in after_the_caller_spoke
    assert after_the_caller_spoke.index("response.cancel") < after_the_caller_spoke.index(
        "conversation.item.truncate"
    )


async def test_a_dropped_connection_recovers_without_ending_the_conversation(
    service: SimulatedRealtimeService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say_something()
        await call.until_heard(1)
        heard_before = len(call.sink.written)
        await session.update_context(LATEST_CONTEXT)

        service.drop_connections()
        # The replacement is only listening once the session has finished restoring it: speech in
        # between belongs to no connection, and waiting on the handshake alone raced that.
        await asyncio.wait_for(
            _until(lambda: metrics.counted(RECONNECTIONS, outcome="succeeded") == 1),
            PATIENCE_SECONDS,
        )
        assert len(service.handshakes) == 2

        call.speaker.say_something()
        await call.until_heard(heard_before + 1)
        assert not call.task.done(), "a recovered connection is the same conversation"

        call.speaker.hang_up()
        await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    await service.wait_until_idle()
    assert service.open_connections == 0
    # The replacement was set up as this session, with the context as it was when the connection
    # dropped rather than as it was when the call began.
    replacement = service.received[1]
    assert replacement.event_types[0] == "session.update"
    assert replacement.instructions[0] == LATEST_CONTEXT


async def test_a_refusal_after_a_drop_ends_the_conversation_with_a_typed_failure(
    service: SimulatedRealtimeService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say_something()
        await call.until_heard(1)

        # The key was revoked while the call was up. Retrying cannot fix that, so the session
        # gives up at once rather than spending its attempts on it.
        service.refuse_authentication()
        service.drop_connections()

        with pytest.raises(ConversationFailedError) as failed:
            await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    assert not failed.value.retryable
    await service.wait_until_idle()
    assert service.open_connections == 0


def test_a_provider_cannot_be_built_without_somewhere_to_connect() -> None:
    with pytest.raises(ConfigurationError, match="SPEECH_ENDPOINT_URL and SPEECH_MODEL"):
        build_speech_provider(make_settings(), metrics=RecordingMetrics())


async def _until(condition: Callable[[], bool]) -> None:
    """Wait for something neither side signals, such as a second handshake reaching the service.

    Polled rather than awaited on an event, because adding an event to production code for a test
    to wait on is the wrong trade; every call is bounded by the caller's own timeout.
    """
    while not condition():  # noqa: ASYNC110 - see the docstring
        await asyncio.sleep(0.01)
