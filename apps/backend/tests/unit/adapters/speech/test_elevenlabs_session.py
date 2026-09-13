"""An ElevenLabs session against an agent that behaves like one.

Resources are proven by counting what is left — tasks still alive, connections still open — and
never by looking at a flag the session sets on itself.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Protocol

import pytest

from letmehandle.adapters.speech.elevenlabs import context as context_module
from letmehandle.adapters.speech.elevenlabs.provider import ElevenLabsSpeechProvider
from letmehandle.adapters.speech.session_support import telemetry
from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
from letmehandle.adapters.speech.session_support.timing import Timekeeping
from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError, ProviderError
from letmehandle.domain.models.audio import (
    SPEECH_WIDEBAND,
    TELEPHONY_NARROWBAND,
    AudioEncoding,
    AudioFormat,
    AudioFrame,
)
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEvent,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)
from tests.support.scripted_elevenlabs_connection import (
    CHUNK_BYTES,
    REPLY_WORDS,
    ScriptedElevenLabsConnection,
    ScriptedElevenLabsService,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from typing import Any

    from tests.support.recording_metrics import RecordingMetrics
    from tests.unit.adapters.speech.conftest import ManualClock, RecordedSleep

TWENTY_MS_WIDEBAND = AudioFrame(bytes(640), SPEECH_WIDEBAND)
INSTRUCTIONS = "answer for someone"


class ProviderFactory(Protocol):
    def __call__(
        self,
        *,
        history_turns: int = ...,
        max_attempts: int = ...,
        audio_ceiling_seconds: float = ...,
        initiation_timeout: float = ...,
    ) -> ElevenLabsSpeechProvider: ...


@pytest.fixture
def service() -> ScriptedElevenLabsService:
    return ScriptedElevenLabsService()


@pytest.fixture
def make_provider(
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
    clock: ManualClock,
    sleep: RecordedSleep,
) -> ProviderFactory:
    def build(
        *,
        history_turns: int = 8,
        max_attempts: int = 3,
        audio_ceiling_seconds: float = 60.0,
        initiation_timeout: float = 2.0,
    ) -> ElevenLabsSpeechProvider:
        return ElevenLabsSpeechProvider(
            service.open,
            metrics,
            languages=("en",),
            input_formats=(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND),
            output_format=SPEECH_WIDEBAND,
            reconnect=ReconnectPolicy(max_attempts, 0.5, 2.0),
            history_turns=history_turns,
            audio_ceiling_seconds=audio_ceiling_seconds,
            initiation_timeout=initiation_timeout,
            timekeeping=Timekeeping(clock=clock, sleep=sleep, draw=lambda: 0.5),
        )

    return build


@pytest.fixture
def provider(make_provider: ProviderFactory) -> ElevenLabsSpeechProvider:
    return make_provider()


async def connect(
    provider: ElevenLabsSpeechProvider,
    input_format: AudioFormat = SPEECH_WIDEBAND,
    locale: str = "en",
) -> SpeechSession:
    return await provider.connect(
        system_context=INSTRUCTIONS,
        voice_id="calm",
        greeting="Hello.",
        locale=locale,
        input_format=input_format,
    )


async def take(events: AsyncIterator[SpeechEvent], count: int) -> list[SpeechEvent]:
    async with asyncio.timeout(2):
        return [await anext(events) for _ in range(count)]


async def remaining(events: AsyncIterator[SpeechEvent]) -> list[SpeechEvent]:
    async with asyncio.timeout(2):
        return [event async for event in events]


def live_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


def override(connection: ScriptedElevenLabsConnection) -> Mapping[str, Any]:
    overrides: Mapping[str, Any] = connection.opening["conversation_config_override"]
    return overrides


def caller_said(text: str) -> TranscriptProduced:
    return TranscriptProduced(text, speaker_is_caller=True, is_final=True)


def agent_said(text: str) -> TranscriptProduced:
    return TranscriptProduced(text, speaker_is_caller=False, is_final=True)


# ------------------------------------------------------------------------------- connecting


async def test_the_conversation_is_opened_with_the_session_s_prompt_greeting_language_and_voice(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider, locale="en-GB"):
        assert service.current.sent_types()[0] == "conversation_initiation_client_data"
        assert override(service.current) == {
            "agent": {
                "prompt": {"prompt": INSTRUCTIONS},
                "language": "en",
                "first_message": "Hello.",
            },
            "tts": {"voice_id": "calm"},
        }


async def test_an_agent_speaking_several_languages_chooses_its_own_voice_for_each(
    service: ScriptedElevenLabsService, metrics: RecordingMetrics
) -> None:
    # A voice sent here would hold for the whole conversation, and a caller the agent followed
    # into Hindi would be answered in Hindi by the English voice.
    provider = ElevenLabsSpeechProvider(
        service.open,
        metrics,
        languages=("en", "hi"),
        input_formats=(SPEECH_WIDEBAND,),
        output_format=SPEECH_WIDEBAND,
    )
    async with await provider.connect(
        system_context=INSTRUCTIONS,
        voice_id="calm",
        greeting="नमस्ते, hello!",
        locale="hi-IN",
        input_format=SPEECH_WIDEBAND,
    ):
        agent = override(service.current)["agent"]
        assert "tts" not in override(service.current)
        assert (agent["language"], agent["first_message"]) == ("hi", "नमस्ते, hello!")
        # The agent's model changes language only through the service's tool, and is told to.
        assert (
            agent["prompt"]["prompt"]
            == f"{INSTRUCTIONS}\n\n{context_module.switching(('en', 'hi'))}"
        )
        assert "language_detection" in context_module.switching(("en", "hi"))


async def test_regional_variants_of_one_language_are_still_one_voice(
    service: ScriptedElevenLabsService, metrics: RecordingMetrics
) -> None:
    provider = ElevenLabsSpeechProvider(
        service.open,
        metrics,
        languages=("en", "en-GB"),
        input_formats=(SPEECH_WIDEBAND,),
        output_format=SPEECH_WIDEBAND,
    )
    async with await provider.connect(
        system_context=INSTRUCTIONS,
        voice_id="calm",
        greeting="Hello.",
        locale="en-GB",
        input_format=SPEECH_WIDEBAND,
    ):
        assert override(service.current)["tts"] == {"voice_id": "calm"}


async def test_connect_waits_for_the_service_to_begin_the_conversation(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    service.begins = False
    connecting = asyncio.create_task(connect(make_provider()))
    await service.wait_for_sent("conversation_initiation_client_data")
    # A ping before the conversation begins still has to be answered, or the service hangs up.
    ping = service.current.ping()
    await service.wait_for_sent("pong")
    assert not connecting.done()

    service.current.begin()
    async with await connecting:
        assert service.current.sent_of("pong") == [{"type": "pong", "event_id": ping}]


async def test_a_conversation_that_never_begins_is_a_retryable_failure(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    service.begins = False
    with pytest.raises(ProviderError, match="did not begin") as raised:
        await connect(make_provider(initiation_timeout=0.01))
    assert raised.value.retryable
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_audio_formats_this_adapter_cannot_read_are_a_permanent_failure(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.output_format = "mp3_44100_128"
    with pytest.raises(ProviderError) as raised:
        await connect(provider)
    assert not raised.value.retryable
    assert service.open_connections == 0


async def test_an_unreadable_event_before_the_conversation_begins_is_counted_not_fatal(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
) -> None:
    service.begins = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("conversation_initiation_client_data")
    service.current.emit({"type": "ping", "ping_event": {"ping_ms": 20}})
    service.current.begin()
    async with await connecting:
        assert metrics.counted(telemetry.STREAM_ERRORS, kind="malformed") == 1
    assert len(service.connections) == 1


@pytest.mark.parametrize("retryable", [True, False])
async def test_a_refused_connection_is_a_typed_error_that_says_whether_to_retry(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService, retryable: bool
) -> None:
    service.refuse_next(retryable=retryable)
    with pytest.raises(ProviderError) as raised:
        await connect(provider)
    assert raised.value.retryable is retryable
    assert live_tasks() == set()


async def test_a_service_error_before_the_conversation_begins_is_counted(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
) -> None:
    service.begins = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("conversation_initiation_client_data")
    service.current.emit({"type": "client_error", "error_event": {"code": 1008}})
    service.current.begin()
    async with await connecting:
        assert metrics.counted(telemetry.STREAM_ERRORS, kind="service") == 1


async def test_cancelling_a_connect_while_the_conversation_begins_releases_the_connection(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.begins = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("conversation_initiation_client_data")

    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting

    assert service.open_connections == 0
    assert live_tasks() == set()


@pytest.mark.parametrize(
    ("locale", "input_format", "voice", "refusal"),
    [
        ("fr", SPEECH_WIDEBAND, "calm", CapabilityNotSupportedError),
        ("en", AudioFormat(AudioEncoding.ALAW, 8_000), "calm", CapabilityNotSupportedError),
        ("en", SPEECH_WIDEBAND, "  ", InvariantError),
    ],
)
async def test_connect_refuses_what_the_provider_cannot_honour(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    locale: str,
    input_format: AudioFormat,
    voice: str,
    refusal: type[Exception],
) -> None:
    with pytest.raises(refusal):
        await provider.connect(
            system_context="c",
            voice_id=voice,
            greeting="Hello.",
            locale=locale,
            input_format=input_format,
        )
    assert service.connections == []


# --------------------------------------------------------------------------------- audio


async def test_caller_audio_reaches_the_agent_in_the_format_the_conversation_began_with(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.answer_audio = False
    service.input_format = "ulaw_8000"
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        await service.wait_for_sent("user_audio_chunk")
        (chunk,) = [event for event in service.current.sent if "user_audio_chunk" in event]
        # Twenty milliseconds of μ-law at 8 kHz is 160 bytes: converted, not passed through.
        assert len(base64.b64decode(chunk["user_audio_chunk"])) == 160


async def test_a_frame_in_a_format_the_session_was_not_opened_for_is_refused(
    provider: ElevenLabsSpeechProvider,
) -> None:
    async with await connect(provider) as session:
        with pytest.raises(ProviderError, match="reached a session opened for"):
            await session.send_audio(AudioFrame(bytes(160), TELEPHONY_NARROWBAND))


async def test_a_reply_arrives_as_speech_words_and_audio_in_the_declared_format(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.output_format = "pcm_24000"
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        words, started, *audio = await take(session.events(), 2 + service.chunks_per_reply)

    assert words == agent_said(REPLY_WORDS)
    assert started == SpeechStarted(by_caller=False)
    assert all(isinstance(each, AudioProduced) for each in audio)
    frames = [each.frame for each in audio if isinstance(each, AudioProduced)]
    assert {frame.format for frame in frames} == {SPEECH_WIDEBAND}
    # Resampled from 24 kHz: about two thirds of the bytes that arrived.
    assert sum(len(frame) for frame in frames) < CHUNK_BYTES * service.chunks_per_reply


async def test_the_caller_s_settled_words_are_reported(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        service.current.caller_said("is anyone there")
        assert await take(session.events(), 1) == [caller_said("is anyone there")]


async def test_audio_too_short_to_make_a_sample_is_held_rather_than_sent_empty(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.answer_audio = False
    service.input_format, service.output_format = "pcm_8000", "pcm_24000"
    async with await connect(provider) as session:
        service.current.audio(size=1)
        await session.send_audio(AudioFrame(b"\x01", SPEECH_WIDEBAND))
        service.current.caller_said("marker")
        # Half a sample of agent audio makes nothing to play, only the start of speech.
        assert await take(session.events(), 2) == [
            SpeechStarted(by_caller=False),
            caller_said("marker"),
        ]
        assert "user_audio_chunk" not in service.current.sent_types()


async def test_a_connection_lost_before_the_conversation_begins_is_a_retryable_failure(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    service.begins = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("conversation_initiation_client_data")
    service.current.emit({"type": "vad_score", "vad_score_event": {"vad_score": 0.2}})
    service.current.drop()
    with pytest.raises(ProviderError) as raised:
        await connecting
    assert raised.value.retryable
    assert service.open_connections == 0


async def test_sending_while_a_replacement_is_opened_is_dropped_not_raised(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService, sleep: RecordedSleep
) -> None:
    # Audio sent into an outage is gone either way; replaying it later answers a caller who has
    # moved on.
    sleep.hold = asyncio.Event()
    async with await connect(provider) as session:
        service.current.drop()
        await sleep.entered.wait()
        await session.send_audio(TWENTY_MS_WIDEBAND)
        sleep.hold.set()
        await service.wait_for_connection_count(2)
    assert "user_audio_chunk" not in service.connections[0].sent_types()


async def test_a_send_on_a_connection_closed_under_it_does_not_raise(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        await service.current.close()
        await session.send_audio(TWENTY_MS_WIDEBAND)
        (failed,) = await remaining(session.events())
    assert isinstance(failed, SessionFailed)


async def test_a_closed_session_refuses_to_be_used(provider: ElevenLabsSpeechProvider) -> None:
    session = await connect(provider)
    await session.close()
    with pytest.raises(ProviderError, match="closed"):
        await session.interrupt()


# ------------------------------------------------------------------------ pings, tools, errors


async def test_a_ping_is_answered_with_its_own_id_and_never_surfaced(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        ping = service.current.ping()
        service.current.caller_said("marker")
        assert await take(session.events(), 1) == [caller_said("marker")]
        assert service.current.sent_of("pong") == [{"type": "pong", "event_id": ping}]


async def test_a_tool_call_is_refused_rather_than_left_waiting(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        service.current.emit(
            {"type": "client_tool_call", "client_tool_call": {"tool_call_id": "waits"}}
        )
        service.current.emit(
            {
                "type": "client_tool_call",
                "client_tool_call": {"tool_call_id": "does-not", "expects_response": False},
            }
        )
        service.current.caller_said("marker")
        await take(session.events(), 1)
        results = service.current.sent_of("client_tool_result")
        assert [(each["tool_call_id"], each["is_error"]) for each in results] == [("waits", True)]


async def test_service_errors_are_counted_and_survived(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
) -> None:
    # A refusal the service can continue past is not worth a caller's conversation; one it cannot
    # continue past closes the connection, and that is the path that ends or replaces it.
    async with await connect(provider) as session:
        for _ in range(5):
            service.current.emit({"type": "client_error", "error_event": {"code": 1003}})
        service.current.emit({"type": "audio", "audio_event": {"event_id": 1}})
        service.current.caller_said("still here")
        assert await take(session.events(), 1) == [caller_said("still here")]
        await session.send_audio(TWENTY_MS_WIDEBAND)

    assert metrics.counted(telemetry.STREAM_ERRORS, kind="service") == 5
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="malformed") == 1


# ----------------------------------------------------------------------------- interruption


async def test_the_service_s_interruption_drops_what_was_held_and_what_arrives_late(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        (first, second, _third) = service.current.reply()
        service.current.interrupt()
        # Sent before the interruption, delivered after it.
        service.current.audio(event_id=second)
        service.current.caller_said("wait")
        service.current.audio()
        await service.wait_until_delivered()

        events = await take(session.events(), 4)

    assert first < second
    assert events == [
        agent_said(REPLY_WORDS),
        SpeechStarted(by_caller=True),
        caller_said("wait"),
        SpeechStarted(by_caller=False),
    ]


async def test_an_interruption_is_acted_on_while_the_consumer_is_behind(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    # A consumer playing in real time is seconds behind a service that speaks faster than that.
    # The interruption and the ping behind the audio must not wait for it to catch up.
    async with await connect(provider) as session:
        service.current.reply(chunks=400)
        ping = service.current.ping()
        service.current.interrupt()
        await service.wait_for_sent("pong")
        await service.wait_until_delivered()

        assert service.current.sent_of("pong") == [{"type": "pong", "event_id": ping}]
        assert await take(session.events(), 2) == [
            agent_said(REPLY_WORDS),
            SpeechStarted(by_caller=True),
        ]


async def test_reading_stops_only_once_the_audio_held_reaches_its_ceiling(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    # Five chunks of twenty milliseconds fill a tenth of a second, so the sixth waits for room
    # and the ping behind it waits too.
    async with await connect(make_provider(audio_ceiling_seconds=0.1)) as session:
        service.current.reply(chunks=8)
        service.current.ping()
        await service.wait_until(lambda: service.current.pending == 3)
        assert service.current.sent_of("pong") == []

        # Words, the start of speech and eight pieces of audio: once the consumer has taken them,
        # reading resumes and the ping is answered.
        await take(session.events(), 10)
        await service.wait_for_sent("pong")


async def test_interrupting_drops_the_rest_of_the_reply_until_the_caller_speaks(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        events = session.events()
        service.current.reply()
        await service.wait_until_delivered()
        await session.interrupt()
        # The service cannot be told to stop, so it goes on, and none of it is played.
        service.current.audio()
        service.current.reply(words="and another thing")
        service.current.caller_said("stop")
        service.current.reply(words="sorry")
        await service.wait_until_delivered()

        assert await take(events, 4) == [
            # Words are not audio: what the agent began to say is still reported.
            agent_said(REPLY_WORDS),
            caller_said("stop"),
            agent_said("sorry"),
            SpeechStarted(by_caller=False),
        ]


async def test_interrupting_before_a_reply_is_read_drops_it_too(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        await session.interrupt()
        service.current.caller_said("marker")
        assert await take(session.events(), 1) == [caller_said("marker")]


# ---------------------------------------------------------------------------------- context


async def test_context_is_sent_as_background_the_next_reply_uses_never_as_the_callers_words(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    # A contextual update is what the service's model reads before its next reply, and does not
    # itself make a reply. A user message would, and would put the words in the caller's mouth:
    # the assistant is told everything the caller says is unverified, so it would be discounted.
    async with await connect(provider) as session:
        service.current.reply()
        await service.wait_until_delivered()
        await session.update_context("the user is being reached")
        await service.wait_for_sent("contextual_update")

        sent = service.current.sent_types()
        assert sent.count("contextual_update") == 1
        assert "user_message" not in sent
        assert "user_activity" not in sent


async def test_context_is_added_without_a_new_connection(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        await session.update_context("the user has joined the call")
        assert service.current.sent_of("contextual_update") == [
            {"type": "contextual_update", "text": "the user has joined the call"}
        ]
        assert len(service.connections) == 1


async def test_a_dropped_conversation_is_replaced_and_told_what_was_said(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    sleep: RecordedSleep,
    metrics: RecordingMetrics,
) -> None:
    async with await connect(provider) as session:
        events = session.events()
        await session.update_context("the user has joined the call")
        service.current.caller_said("can you move my appointment")
        service.current.reply(words="let me tell you all the times I have free")
        service.current.interrupt()
        service.current.correct("let me tell you all the times I have free", "let me tell you")
        service.current.caller_said("thursday")
        service.current.reply(chunks=0, words="thursday it is")
        await take(events, 5)
        await service.wait_until_delivered()

        service.current.drop()
        await service.wait_for_connection_count(2)
        await service.wait_for_sent("conversation_initiation_client_data", connection=2)
        service.current.caller_said("thanks")
        assert await take(events, 1) == [caller_said("thanks")]

    agent = override(service.connections[1])["agent"]
    # Asked not to greet a caller who has been talking for a while.
    assert agent["first_message"] == ""
    assert agent["prompt"]["prompt"] == (
        f"{INSTRUCTIONS}\n\n"
        f"{context_module._UPDATES_HEADING}\nthe user has joined the call\n\n"
        f"{context_module._RESUMED_HEADING}\n"
        "Caller: can you move my appointment\n"
        "You: let me tell you\n"
        "Caller: thursday\n"
        "You: thursday it is"
    )
    assert sleep.delays == [0.375]
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 1


async def test_a_reply_cut_to_nothing_is_forgotten(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    async with await connect(make_provider()) as session:
        service.current.caller_said("hello")
        service.current.reply(chunks=0, words="well")
        service.current.correct("well", " ")
        # A correction of words this session did not remember changes nothing.
        service.current.correct("something never said", "something")
        await take(session.events(), 2)
        await service.wait_until_delivered()
        service.current.drop()
        await service.wait_for_sent("conversation_initiation_client_data", connection=2)

    prompt = override(service.connections[1])["agent"]["prompt"]["prompt"]
    assert prompt.endswith(f"{context_module._RESUMED_HEADING}\nCaller: hello")


async def test_what_a_replacement_is_told_is_bounded(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    async with await connect(make_provider(history_turns=2)) as session:
        for text in ("one", "two", "three"):
            service.current.caller_said(text)
        for _ in range(context_module.CONTEXT_UPDATES_KEPT + 1):
            await session.update_context("update")
        await take(session.events(), 3)
        service.current.drop()
        await service.wait_for_sent("conversation_initiation_client_data", connection=2)

    prompt = override(service.connections[1])["agent"]["prompt"]["prompt"]
    assert prompt.endswith("Caller: two\nCaller: three")
    assert prompt.count("update") == context_module.CONTEXT_UPDATES_KEPT


async def test_a_context_update_made_while_a_replacement_begins_reaches_it(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        service.stalled = asyncio.Event()
        service.current.drop()
        await service.wait_for_connection_count(2)

        # Written into neither the replacement's prompt, which is already on its way, nor the
        # dead connection: it must be sent once the replacement has begun.
        await session.update_context("the user has joined the call")
        service.stalled.set()
        await service.wait_for_sent("contextual_update", connection=2)

    assert (
        "the user has joined" not in override(service.connections[1])["agent"]["prompt"]["prompt"]
    )


async def test_the_service_hanging_up_is_reconnected_through(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        service.current.hang_up()
        await service.wait_for_connection_count(2)
        await session.send_audio(TWENTY_MS_WIDEBAND)
        assert isinstance((await take(session.events(), 1))[0], TranscriptProduced)


async def test_a_permanent_failure_ends_the_session_with_a_typed_error(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        service.current.drop(retryable=False)
        assert await remaining(session.events()) == [SessionFailed("dropped", retryable=False)]
        with pytest.raises(ProviderError, match="dropped"):
            await session.send_audio(TWENTY_MS_WIDEBAND)
        with pytest.raises(ProviderError):
            await session.update_context("too late")
        with pytest.raises(ProviderError):
            await session.interrupt()

    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_reconnection_gives_up_after_its_attempts(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    sleep: RecordedSleep,
) -> None:
    async with await connect(provider) as session:
        service.refuse_next(retryable=True, times=3)
        service.current.drop()
        (failed,) = await remaining(session.events())

    assert failed == SessionFailed("the connection could not be restored in 3 attempts", False)
    assert sleep.delays == [0.375, 0.75, 1.5]


async def test_a_service_that_begins_and_drops_every_conversation_runs_out_of_attempts(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
) -> None:
    async with await connect(provider) as session:
        service.hangs_up_after_beginning = True
        service.current.hang_up()
        (failed,) = await remaining(session.events())

    # Each replacement was accepted, and none delivered anything: three attempts, not forever.
    assert isinstance(failed, SessionFailed)
    assert len(service.connections) == 1 + 3
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 3
    assert service.open_connections == 0


async def test_a_replacement_that_delivers_something_earns_back_its_attempts(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async with await connect(provider) as session:
        for count in range(2, 7):
            service.current.drop()
            await service.wait_for_sent("conversation_initiation_client_data", connection=count)
            service.current.caller_said(f"still here {count}")
            await take(session.events(), 1)

    assert len(service.connections) == 6


# ------------------------------------------------------------------------------- resources


async def test_a_defect_in_the_reader_ends_the_stream_and_is_raised_on_close(
    provider: ElevenLabsSpeechProvider,
    service: ScriptedElevenLabsService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_: ScriptedElevenLabsConnection) -> None:
        raise RuntimeError("a connection that broke its own contract")

    session = await connect(provider)
    monkeypatch.setattr(ScriptedElevenLabsConnection, "receive", broken)
    service.current.hang_up()

    events = await remaining(session.events())
    assert events == [SessionFailed("the session stopped unexpectedly", retryable=False)]
    with pytest.raises(RuntimeError):
        await session.close()
    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_twice_ends_the_stream_for_every_reader(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    session = await connect(provider)
    service.current.reply()
    await service.wait_until_delivered()
    await session.close()
    await session.close()
    assert await remaining(session.events()) == []
    assert await remaining(session.events()) == []
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_cancelling_the_consumer_mid_reply_leaves_nothing(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    heard = asyncio.Event()

    async def converse() -> None:
        async with await connect(provider) as session:
            service.chunks_per_reply = 50
            await session.send_audio(TWENTY_MS_WIDEBAND)
            async for event in session.events():
                if isinstance(event, AudioProduced):
                    heard.set()

    task = asyncio.create_task(converse())
    await heard.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_cancelling_during_reconnection_backoff_leaves_nothing(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService, sleep: RecordedSleep
) -> None:
    sleep.hold = asyncio.Event()

    async def converse() -> None:
        async with await connect(provider) as session:
            service.current.drop()
            async for _ in session.events():
                pass

    task = asyncio.create_task(converse())
    await sleep.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_cancelling_while_a_replacement_begins_leaves_nothing(
    provider: ElevenLabsSpeechProvider, service: ScriptedElevenLabsService
) -> None:
    async def converse() -> None:
        async with await connect(provider) as session:
            service.begins = False
            service.current.drop()
            async for _ in session.events():
                pass

    task = asyncio.create_task(converse())
    await service.wait_for_sent("conversation_initiation_client_data", connection=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_while_the_reader_waits_for_room_leaves_nothing(
    make_provider: ProviderFactory, service: ScriptedElevenLabsService
) -> None:
    session = await connect(make_provider(audio_ceiling_seconds=0.02))
    service.current.reply(chunks=5)
    await service.wait_until(lambda: service.current.pending == 3)
    await session.close()
    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_while_the_consumer_waits_ends_its_iteration(
    provider: ElevenLabsSpeechProvider,
) -> None:
    session = await connect(provider)
    consumer = asyncio.create_task(remaining(session.events()))
    await asyncio.sleep(0)
    await session.close()
    assert await consumer == []


# -------------------------------------------------------------------------------- provider


def test_it_declares_what_the_protocol_lets_it_implement(
    provider: ElevenLabsSpeechProvider,
) -> None:
    capabilities = provider.capabilities
    assert provider.name == "elevenlabs"
    # Barge-in is the service's own; context updates are additive; reconnection is a new
    # conversation reminded of the old one. Each is declared, and each limit is documented.
    assert capabilities.barge_in
    assert capabilities.context_updates_mid_session
    assert capabilities.reconnection
    assert capabilities.languages == ("en",)
    assert capabilities.input_formats == (SPEECH_WIDEBAND, TELEPHONY_NARROWBAND)
    assert capabilities.output_format == SPEECH_WIDEBAND


@pytest.mark.parametrize(
    ("languages", "ceiling", "timeout"),
    [((), 1.0, 1.0), (("en",), 0.0, 1.0), (("en",), 1.0, 0.0)],
)
def test_a_configuration_it_cannot_honour_is_refused_at_construction(
    service: ScriptedElevenLabsService,
    metrics: RecordingMetrics,
    languages: tuple[str, ...],
    ceiling: float,
    timeout: float,
) -> None:
    with pytest.raises(InvariantError):
        ElevenLabsSpeechProvider(
            service.open,
            metrics,
            languages=languages,
            input_formats=(SPEECH_WIDEBAND,),
            output_format=SPEECH_WIDEBAND,
            audio_ceiling_seconds=ceiling,
            initiation_timeout=timeout,
        )


async def test_the_defaults_are_usable(
    service: ScriptedElevenLabsService, metrics: RecordingMetrics
) -> None:
    provider = ElevenLabsSpeechProvider(
        service.open,
        metrics,
        languages=("en",),
        input_formats=(SPEECH_WIDEBAND,),
        output_format=SPEECH_WIDEBAND,
    )
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        assert (await take(session.events(), 1)) == [agent_said(REPLY_WORDS)]
