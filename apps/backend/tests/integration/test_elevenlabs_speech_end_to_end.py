"""A spoken conversation with an ElevenLabs agent, end to end, with no account and no call.

Everything here is the real code a deployment runs — the settings, the bootstrap, the ElevenLabs
session, the websocket connection and the conversation use case — talking over a real socket to a
simulated agent on loopback. The simulation holds the client to what the real service holds it to:
pings answered, overrides allowed, events only as configured. Each test asserts on what the
service received as well as on what the caller heard, so that deleting the session's own
interruption handling or its restoration of a dropped conversation fails a test here, rather than
being covered for by the conversation discarding its speaker anyway.
"""

from __future__ import annotations

import asyncio
import math
import struct
from typing import TYPE_CHECKING, Final

import pytest

from letmehandle.adapters.clock import SystemClock
from letmehandle.adapters.speech.session_support.telemetry import TIME_TO_FIRST_AUDIO
from letmehandle.application.speech.conversation import (
    Conversation,
    ConversationEnd,
    ConversationFailedError,
    Transcript,
    TranscriptTurn,
)
from letmehandle.bootstrap import build_speech_provider
from letmehandle.config.settings import ConfigurationError, SpeechProviderName
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.ports.audio_io import AudioSource
from tests.support.audio import SAMPLES_PER_FRAME, RecordingSink
from tests.support.config import EXAMPLE_DEFAULT_VOICE, make_settings
from tests.support.recording_metrics import RecordingMetrics
from tests.support.simulated_elevenlabs_service import (
    DEFAULT_CLIENT_EVENTS,
    OVERRIDES_THIS_ADAPTER_NEEDS,
    SIMULATED_AGENT_ID,
    SIMULATED_API_KEY,
    SIMULATED_REPLY,
    SIMULATED_TRANSCRIPT,
    Opening,
    SimulatedElevenLabsService,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from letmehandle.domain.ports.speech import SpeechProvider, SpeechSession

SYSTEM_CONTEXT: Final = "You answer calls for somebody who is busy."
# How long a turn hums before falling silent. The simulation hears any non-silent audio as speech
# and a silent frame after it as the end of the turn, which is all a turn needs to be.
TURN_FRAMES: Final = 10
SILENCE: Final = AudioFrame(bytes(2 * SAMPLES_PER_FRAME), SPEECH_WIDEBAND)
# Long enough for a response over loopback, short enough that a hang fails the test promptly.
PATIENCE_SECONDS: Final = 10.0


def turn_frames(turn: int) -> list[AudioFrame]:
    """What the caller says in one turn: a tone of its own pitch, so no other turn repeats a frame.

    The agent speaks the caller's audio back, so a frame the speaker plays says which turn's reply
    it came from — which is how a test tells an interrupted reply from the one after it. The shared
    tone repeats itself every few frames, which is why each turn is not simply more of it.
    """
    frequency = 300.0 + 150.0 * turn
    rate = SPEECH_WIDEBAND.sample_rate_hz
    frames = []
    for index in range(TURN_FRAMES):
        first = index * SAMPLES_PER_FRAME
        samples = [
            round(8_000 * math.sin(2 * math.pi * frequency * n / rate))
            for n in range(first, first + SAMPLES_PER_FRAME)
        ]
        frames.append(AudioFrame(struct.pack(f"<{len(samples)}h", *samples), SPEECH_WIDEBAND))
    return frames


class Speaker(AudioSource):
    """Somebody who says what the test tells them to, then stays quiet on the line."""

    def __init__(self) -> None:
        self._frames: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    def say(self, turn: int) -> None:
        for frame in turn_frames(turn):
            self._frames.put_nowait(frame)
        self._frames.put_nowait(SILENCE)

    def hang_up(self) -> None:
        self._frames.put_nowait(None)

    async def frames(self) -> AsyncIterator[AudioFrame]:
        while (frame := await self._frames.get()) is not None:
            yield frame


@pytest.fixture
async def service() -> AsyncIterator[SimulatedElevenLabsService]:
    async with SimulatedElevenLabsService() as running:
        yield running


def provider_for(service: SimulatedElevenLabsService, metrics: RecordingMetrics) -> SpeechProvider:
    settings = make_settings(
        speech_provider=SpeechProviderName.ELEVENLABS,
        speech_endpoint_url=service.url,
        speech_agent_id=SIMULATED_AGENT_ID,
        speech_api_key=SIMULATED_API_KEY,
    )
    return build_speech_provider(settings, metrics=metrics)


async def connect(provider: SpeechProvider) -> SpeechSession:
    return await provider.connect(
        system_context=SYSTEM_CONTEXT,
        voice_id=EXAMPLE_DEFAULT_VOICE,
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

    def heard(self) -> list[bytes]:
        return [frame.data for frame in self.sink.written]


async def test_a_conversation_is_held_end_to_end_over_a_real_socket(
    service: SimulatedElevenLabsService,
) -> None:
    metrics = RecordingMetrics()
    before = len(asyncio.all_tasks())

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say(0)
        await call.until_heard(TURN_FRAMES)
        # Kept on the line for well past the service's patience with an unanswered ping.
        await asyncio.wait_for(_until(lambda: len(service.answered_pings) >= 20), PATIENCE_SECONDS)

        call.speaker.hang_up()
        assert await asyncio.wait_for(call.task, PATIENCE_SECONDS) is ConversationEnd.SPEAKER_GONE

    await service.wait_until_idle()
    # The agent answered with what it heard, the handshake carried the agent and the key, the
    # conversation was opened as this session, and it was never dropped for want of a pong.
    assert call.heard() == [frame.data for frame in turn_frames(0)]
    assert call.transcript.turns == (
        TranscriptTurn(SIMULATED_TRANSCRIPT, speaker_is_caller=True),
        TranscriptTurn(SIMULATED_REPLY, speaker_is_caller=False),
    )
    assert [(each.api_key, each.agent_id) for each in service.handshakes] == [
        (SIMULATED_API_KEY, SIMULATED_AGENT_ID)
    ]
    assert service.openings == [
        Opening(
            prompt=SYSTEM_CONTEXT,
            first_message=None,
            language="en",
            voice_id=EXAMPLE_DEFAULT_VOICE,
        )
    ]
    assert metrics.observed(TIME_TO_FIRST_AUDIO)
    assert service.open_connections == 0
    assert len(asyncio.all_tasks()) == before


async def test_the_agent_interrupted_by_the_caller_is_not_heard_again(
    service: SimulatedElevenLabsService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        # A speaker still busy with the first frame of a reply, while the rest of it has arrived
        # and is waiting: the ordinary state of a speaker playing in real time.
        call.sink.hold()
        service.hold_next_reply(after=6)
        call.speaker.say(0)
        await asyncio.wait_for(call.sink.held.wait(), PATIENCE_SECONDS)
        await asyncio.wait_for(_until(lambda: service.holding), PATIENCE_SECONDS)

        # The caller talks over it. A ping the service sends after its interruption being
        # answered proves the session has read the interruption, with the speaker still stuck.
        call.speaker.say(1)
        await asyncio.wait_for(_until(lambda: bool(service.interruptions)), PATIENCE_SECONDS)
        await asyncio.wait_for(
            _until(lambda: max(service.answered_pings, default=0) > service.interruptions[0]),
            PATIENCE_SECONDS,
        )
        call.sink.release()
        await call.until_heard(1 + TURN_FRAMES)

        call.speaker.hang_up()
        await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    # Only the frame already at the speaker was played. The five waiting behind it, and the one
    # the service had on its way when it stopped, were dropped by the session; the speaker's own
    # buffer was cleared at once; and the next thing played was the reply to what interrupted.
    assert call.sink.discarded_after == [1]
    assert call.heard() == [turn_frames(0)[0].data, *(frame.data for frame in turn_frames(1))]


async def test_a_dropped_conversation_is_replaced_by_one_told_what_was_said(
    service: SimulatedElevenLabsService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say(0)
        await call.until_heard(TURN_FRAMES)
        await session.update_context("the user has joined the call")
        await asyncio.wait_for(_until(lambda: bool(service.context_updates)), PATIENCE_SECONDS)

        answered = len(service.answered_pings)
        service.drop_connections()
        await asyncio.wait_for(_until(lambda: len(service.openings) == 2), PATIENCE_SECONDS)
        # Audio said into the outage is dropped, not replayed, so the caller speaks again only once
        # the new conversation is answering its pings.
        await asyncio.wait_for(
            _until(lambda: len(service.answered_pings) > answered), PATIENCE_SECONDS
        )

        call.speaker.say(1)
        await call.until_heard(2 * TURN_FRAMES)
        assert not call.task.done(), "a replaced conversation is the same call"

        call.speaker.hang_up()
        await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    await service.wait_until_idle()
    resumed = service.openings[1]
    # The service cannot resume a conversation, so the new one is told it in its prompt, and is
    # asked not to greet a caller who has been talking for a while.
    assert resumed.first_message == ""
    assert resumed.prompt is not None
    assert resumed.prompt.startswith(SYSTEM_CONTEXT)
    assert "the user has joined the call" in resumed.prompt
    assert f"Caller: {SIMULATED_TRANSCRIPT}\nYou: {SIMULATED_REPLY}" in resumed.prompt
    assert service.open_connections == 0


async def test_an_agent_that_does_not_allow_the_overrides_ends_the_conversation(
    metrics: RecordingMetrics,
) -> None:
    # A deployment whose agent allows the prompt, language and voice but not the first message:
    # the first conversation opens, and the replacement for a dropped one is refused.
    async with SimulatedElevenLabsService(
        allowed_overrides=OVERRIDES_THIS_ADAPTER_NEEDS - {"first_message"}
    ) as service:
        async with await connect(provider_for(service, metrics)) as session:
            call = Call(session, metrics)
            service.drop_connections()
            with pytest.raises(ConversationFailedError) as failed:
                await asyncio.wait_for(call.task, PATIENCE_SECONDS)
        await service.wait_until_idle()

    assert not failed.value.retryable
    assert len(service.handshakes) == 2


async def test_an_agent_that_sends_no_caller_transcripts_still_holds_a_conversation(
    metrics: RecordingMetrics,
) -> None:
    async with (
        SimulatedElevenLabsService(
            client_events=DEFAULT_CLIENT_EVENTS - {"user_transcript"}
        ) as service,
        await connect(provider_for(service, metrics)) as session,
    ):
        call = Call(session, metrics)
        call.speaker.say(0)
        await call.until_heard(TURN_FRAMES)
        call.speaker.hang_up()
        await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    assert call.transcript.turns == (TranscriptTurn(SIMULATED_REPLY, speaker_is_caller=False),)


async def test_a_refusal_after_a_drop_ends_the_conversation_with_a_typed_failure(
    service: SimulatedElevenLabsService,
) -> None:
    metrics = RecordingMetrics()

    async with await connect(provider_for(service, metrics)) as session:
        call = Call(session, metrics)
        call.speaker.say(0)
        await call.until_heard(1)

        # The key was revoked while the call was up. Retrying cannot fix that, so the session
        # gives up at once rather than spending its attempts on it.
        service.refuse_authentication()
        service.drop_connections()

        with pytest.raises(ConversationFailedError) as failed:
            await asyncio.wait_for(call.task, PATIENCE_SECONDS)

    assert not failed.value.retryable
    assert len(service.handshakes) == 2
    await service.wait_until_idle()
    assert service.open_connections == 0


async def test_a_refused_key_is_a_typed_failure_before_any_conversation(
    service: SimulatedElevenLabsService,
) -> None:
    service.refuse_authentication()
    with pytest.raises(ProviderError) as refused:
        await connect(provider_for(service, RecordingMetrics()))
    assert not refused.value.retryable
    assert "401" in str(refused.value)
    assert SIMULATED_API_KEY not in str(refused.value)


def test_a_provider_cannot_be_built_without_an_agent_to_talk_to() -> None:
    settings = make_settings(
        speech_provider=SpeechProviderName.ELEVENLABS,
        speech_endpoint_url="wss://speech.example.com/v1/convai/conversation",
    )
    with pytest.raises(ConfigurationError, match="SPEECH_AGENT_ID must be set"):
        build_speech_provider(settings, metrics=RecordingMetrics())


@pytest.fixture
def metrics() -> RecordingMetrics:
    return RecordingMetrics()


async def _until(condition: Callable[[], bool]) -> None:
    """Wait for something neither side signals, such as a second conversation reaching the service.

    Polled rather than awaited on an event, because adding an event to production code for a test
    to wait on is the wrong trade; every call is bounded by the caller's own timeout.
    """
    while not condition():  # noqa: ASYNC110 - see the docstring
        await asyncio.sleep(0.01)
