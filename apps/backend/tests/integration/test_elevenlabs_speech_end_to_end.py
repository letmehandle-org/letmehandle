"""A spoken conversation with a simulated ElevenLabs agent on loopback, through the real stack."""

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
# Frames of tone per turn; the simulation ends a turn at the first silent frame after speech.
TURN_FRAMES: Final = 10
SILENCE: Final = AudioFrame(bytes(2 * SAMPLES_PER_FRAME), SPEECH_WIDEBAND)
# Long enough for a response over loopback, short enough that a hang fails the test promptly.
PATIENCE_SECONDS: Final = 10.0


def turn_frames(turn: int) -> list[AudioFrame]:
    """One turn of caller audio: a tone of its own pitch, so every reply frame names its turn."""
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
    # The agent echoed the caller, the handshake carried agent and key, and no pong was missed.
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
            first_message="Hello.",
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
        # The speaker is busy with a reply's first frame while the rest waits behind it.
        call.sink.hold()
        service.hold_next_reply(after=6)
        call.speaker.say(0)
        await asyncio.wait_for(call.sink.held.wait(), PATIENCE_SECONDS)
        await asyncio.wait_for(_until(lambda: service.holding), PATIENCE_SECONDS)

        # The caller talks over it; a ping answered after the interruption shows it was read.
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

    # Only the frame at the speaker played; queued frames were dropped; the next reply followed.
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
        # Audio during the outage is dropped, so the caller speaks once pings are answered again.
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
    # The new conversation carries the history in its prompt and opens without a greeting.
    assert resumed.first_message == ""
    assert resumed.prompt is not None
    assert resumed.prompt.startswith(SYSTEM_CONTEXT)
    assert "the user has joined the call" in resumed.prompt
    assert f"Caller: {SIMULATED_TRANSCRIPT}\nYou: {SIMULATED_REPLY}" in resumed.prompt
    assert service.open_connections == 0


async def test_an_agent_that_does_not_allow_the_overrides_is_refused_before_a_conversation(
    metrics: RecordingMetrics,
) -> None:
    # An agent refusing the first-message override opens no conversation and nothing is retried.
    async with SimulatedElevenLabsService(
        allowed_overrides=OVERRIDES_THIS_ADAPTER_NEEDS - {"first_message"}
    ) as service:
        with pytest.raises(ProviderError) as refused:
            await connect(provider_for(service, metrics))
        await service.wait_until_idle()

    assert not refused.value.retryable
    assert len(service.handshakes) == 1


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

        # A revoked key ends the session at once instead of spending its reconnection attempts.
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
    """Polls until a condition neither side signals holds, bounded by the caller's timeout."""
    while not condition():  # noqa: ASYNC110 - a bounded poll
        await asyncio.sleep(0.01)
