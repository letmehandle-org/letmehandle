"""A GPT-Live session against a service that behaves like one.

Resources are proven by counting what is left — tasks still alive, connections still open — and
never by looking at a flag the session sets on itself.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.speech.gpt_live import language
from letmehandle.adapters.speech.gpt_live.context import APPEND_CHARACTERS
from letmehandle.adapters.speech.gpt_live.provider import GptLiveSpeechProvider
from letmehandle.adapters.speech.gpt_live.session import NO_DELEGATE
from letmehandle.adapters.speech.session_support import telemetry
from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError, ProviderError
from letmehandle.domain.models.audio import (
    SPEECH_WIDEBAND,
    TELEPHONY_NARROWBAND,
    AudioFormat,
    AudioFrame,
)
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEnded,
    SpeechEvent,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)
from tests.support.scripted_gpt_live_connection import (
    REPLY_TRANSCRIPT,
    USAGE_SECONDS,
    ScriptedGptLiveConnection,
    ScriptedGptLiveService,
)
from tests.unit.adapters.speech.gpt_live.conftest import PCM_24K, PCM_48K, TELEPHONY_ALAW

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.support.recording_metrics import RecordingMetrics
    from tests.unit.adapters.speech.conftest import ManualClock, RecordedSleep
    from tests.unit.adapters.speech.gpt_live.conftest import ProviderFactory

TWENTY_MS_NARROWBAND = AudioFrame(bytes(range(160)), TELEPHONY_NARROWBAND)
HINDI = "नमस्ते, मेरा पार्सल कहाँ है?"


async def connect(
    provider: GptLiveSpeechProvider,
    *,
    locale: str = "en",
    input_format: AudioFormat = TELEPHONY_NARROWBAND,
    context: str = "answer for someone",
) -> SpeechSession:
    return await provider.connect(
        system_context=context,
        voice_id="gleam",
        greeting="Hello, how can I help?",
        locale=locale,
        input_format=input_format,
    )


async def next_of[E: SpeechEvent](events: AsyncIterator[SpeechEvent], kind: type[E]) -> E:
    async with asyncio.timeout(2):
        async for event in events:
            if isinstance(event, kind):
                return event
    raise AssertionError(f"the stream ended without a {kind.__name__}")


async def spoken_until(events: AsyncIterator[SpeechEvent], last: SpeechEvent) -> list[SpeechEvent]:
    """Every event but audio, up to and including `last`."""
    seen: list[SpeechEvent] = []
    async with asyncio.timeout(2):
        async for event in events:
            if not isinstance(event, AudioProduced):
                seen.append(event)
            if event == last:
                return seen
    raise AssertionError(f"the stream ended before {last}")


async def remaining(events: AsyncIterator[SpeechEvent]) -> list[SpeechEvent]:
    async with asyncio.timeout(2):
        return [event async for event in events]


def live_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


def said(text: str, *, by_caller: bool, final: bool) -> TranscriptProduced:
    return TranscriptProduced(text, speaker_is_caller=by_caller, is_final=final)


# ------------------------------------------------------------------------------- connecting


async def test_a_session_is_started_before_anything_else_and_then_greets(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider):
        sent = service.current.sent
        assert sent[0] == {
            "type": "session.start",
            "session": {
                "model": "a-live-model",
                "instructions": "answer for someone",
                "audio": {
                    "format": {"type": "audio/pcmu", "rate": 8000},
                    "output": {"voice": "gleam"},
                },
                "delegation": {"type": "client"},
            },
        }
        assert sent[1] == {
            "type": "session.instructions.append",
            "delegation_id": None,
            "content": language.opening("en", "Hello, how can I help?"),
        }
        assert sent[2] == {
            "type": "session.commentary.append",
            "delegation_id": None,
            "content": language.BEGIN,
        }
        assert 'with: "Hello, how can I help?"' in str(sent[1]["content"])


async def test_a_hindi_session_is_told_its_language_in_hindi(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    provider = make_provider(languages=("en", "hi"))
    async with await connect(provider, locale="hi-IN"):
        [opening] = service.current.appended_instructions()
        assert opening.startswith("इस बातचीत में हिंदी में ही बात करें")
        assert '"Hello, how can I help?"' in opening


@pytest.mark.parametrize(
    ("input_format", "wire"),
    [
        (TELEPHONY_NARROWBAND, {"type": "audio/pcmu", "rate": 8000}),
        (TELEPHONY_ALAW, {"type": "audio/pcma", "rate": 8000}),
        (SPEECH_WIDEBAND, {"type": "audio/pcm", "rate": 16000}),
        (PCM_24K, {"type": "audio/pcm", "rate": 24000}),
        (PCM_48K, {"type": "audio/pcm", "rate": 24000}),
    ],
)
async def test_the_session_speaks_the_format_its_audio_arrives_in_where_it_can(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    input_format: AudioFormat,
    wire: dict[str, object],
) -> None:
    async with await connect(provider, input_format=input_format):
        assert service.current.sent[0]["session"]["audio"]["format"] == wire


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        ({"type": "invalid_request_error", "code": "unknown_voice"}, False),
        ({"type": "rate_limit_error", "code": "rate_limit_exceeded"}, True),
        ({"code": None}, False),
    ],
)
async def test_a_session_the_service_refuses_is_a_typed_error_that_says_whether_to_retry(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    metrics: RecordingMetrics,
    error: dict[str, object],
    retryable: bool,
) -> None:
    service.start_errors.append(error)
    with pytest.raises(ProviderError) as raised:
        await connect(provider)
    assert raised.value.retryable is retryable
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="service") == 1
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_a_service_that_never_starts_the_session_times_out_worth_retrying(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    service.starts = False
    with pytest.raises(ProviderError) as raised:
        await connect(make_provider(start_timeout=0.05))
    assert raised.value.retryable
    assert service.open_connections == 0


@pytest.mark.parametrize(("reason", "retryable"), [("content", False), ("expired", True)])
async def test_a_session_closed_as_it_starts_is_refused(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, reason: str, retryable: bool
) -> None:
    service.starts = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("session.start")
    service.current.emit({"type": "session.usage.updated", "usage": {"seconds": 0}})
    service.current.close_session(reason)
    with pytest.raises(ProviderError) as raised:
        await connecting
    assert raised.value.retryable is retryable


async def test_a_connection_lost_as_the_session_starts_is_worth_retrying(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.starts = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("session.start")
    service.current.drop()
    with pytest.raises(ProviderError) as raised:
        await connecting
    assert raised.value.retryable
    assert service.open_connections == 0


async def test_a_refused_connection_is_a_typed_error(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.refuse_next(retryable=True)
    with pytest.raises(ProviderError) as raised:
        await connect(provider)
    assert raised.value.retryable
    assert live_tasks() == set()


async def test_cancelling_a_connect_while_the_session_starts_releases_the_connection(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.starts = False
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_sent("session.start")
    connecting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await connecting
    assert service.open_connections == 0


async def test_connect_refuses_what_the_provider_cannot_honour(
    provider: GptLiveSpeechProvider,
) -> None:
    with pytest.raises(CapabilityNotSupportedError):
        await connect(provider, locale="fr")
    with pytest.raises(InvariantError):
        await provider.connect(
            system_context="c",
            voice_id=" ",
            greeting="Hello.",
            locale="en",
            input_format=TELEPHONY_NARROWBAND,
        )


# ------------------------------------------------------------------------------------ audio


async def test_a_phone_call_s_audio_reaches_the_service_unconverted(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.answer_audio = False
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_NARROWBAND)
        [append] = service.current.sent_of("session.input_audio.append")
        assert base64.b64decode(append["audio"]) == TWENTY_MS_NARROWBAND.data


async def test_audio_in_a_format_the_protocol_cannot_carry_is_converted(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.answer_audio = False
    async with await connect(provider, input_format=PCM_48K) as session:
        await session.send_audio(AudioFrame(bytes(960), PCM_48K))
        [append] = service.current.sent_of("session.input_audio.append")
        assert len(base64.b64decode(append["audio"])) == 480


async def test_audio_too_short_to_make_a_sample_is_held_rather_than_sent_empty(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.answer_audio = False
    async with await connect(provider, input_format=PCM_48K) as session:
        await session.send_audio(AudioFrame(b"\x01", PCM_48K))
        assert service.current.sent_of("session.input_audio.append") == []


async def test_half_a_sample_from_the_service_is_neither_played_nor_heard_as_speech(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider, input_format=SPEECH_WIDEBAND) as session:
        half = base64.b64encode(b"\x01").decode("ascii")
        service.current.emit({"type": "session.output_audio.delta", "delta": half})
        service.current.caller_said("hello")
        taken: list[SpeechEvent] = []
        async with asyncio.timeout(2):
            async for event in session.events():
                taken.append(event)
                if isinstance(event, TranscriptProduced):
                    break
    # The caller's silence is played; half a sample of the assistant is not, and is not speech.
    assert all(len(event.frame.data) == 160 for event in taken if isinstance(event, AudioProduced))
    assert [event for event in taken if not isinstance(event, AudioProduced)] == [
        SpeechStarted(by_caller=True),
        said("hello", by_caller=True, final=False),
    ]


async def test_a_frame_in_a_format_the_session_was_not_opened_for_is_refused(
    provider: GptLiveSpeechProvider,
) -> None:
    async with await connect(provider) as session:
        with pytest.raises(ProviderError):
            await session.send_audio(AudioFrame(bytes(640), SPEECH_WIDEBAND))


async def test_the_assistant_s_voice_arrives_between_the_start_and_end_of_its_speech(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.reply(silence=2_000)
        events = session.events()
        taken: list[SpeechEvent] = []
        async with asyncio.timeout(2):
            async for event in events:
                taken.append(event)
                if isinstance(event, TranscriptProduced) and event.is_final:
                    break

    assert taken[0] == SpeechStarted(by_caller=False)
    audio = [event for event in taken if isinstance(event, AudioProduced)]
    assert all(event.frame.format == TELEPHONY_NARROWBAND for event in audio)
    assert [event for event in taken if not isinstance(event, AudioProduced)] == [
        SpeechStarted(by_caller=False),
        said(REPLY_TRANSCRIPT, by_caller=False, final=False),
        SpeechEnded(by_caller=False),
        said(REPLY_TRANSCRIPT, by_caller=False, final=True),
    ]
    # The quiet before the end of speech is played too: it is part of what the caller hears.
    assert len(audio) > 3


async def test_the_assistant_s_voice_is_produced_in_the_declared_output_format(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    provider = make_provider(output_format=SPEECH_WIDEBAND)
    async with await connect(provider) as session:
        service.current.reply()
        produced = await next_of(session.events(), AudioProduced)
    assert produced.frame.format == SPEECH_WIDEBAND
    assert 600 <= len(produced.frame.data) <= 640


async def test_a_consumer_that_stops_taking_audio_stops_reading_at_the_ceiling(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    provider = make_provider(audio_ceiling_seconds=0.1)
    async with await connect(provider):
        connection = service.current
        connection.reply(speech=20)
        # Five pieces fill the ceiling; the reader waits there and leaves the rest unread.
        await service.wait_until(lambda: connection.pending <= 16)
        await asyncio.sleep(0.05)
        assert connection.pending >= 14


# ------------------------------------------------------------------------------ transcripts


async def test_words_settle_into_turns_once_their_speaker_has_been_quiet(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.caller_said("book a table")
        connection.reply(transcript="for how many", silence=2_000)
        events = await spoken_until(
            session.events(), said("for how many", by_caller=False, final=True)
        )

    assert events == [
        SpeechStarted(by_caller=True),
        said("book a table", by_caller=True, final=False),
        SpeechStarted(by_caller=False),
        said("for how many", by_caller=False, final=False),
        SpeechEnded(by_caller=False),
        SpeechEnded(by_caller=True),
        said("book a table", by_caller=True, final=True),
        said("for how many", by_caller=False, final=True),
    ]


async def test_an_utterance_in_fragments_settles_as_one_turn(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.caller_said("book a")
        connection.caller_said(" table", after_ms=200)
        connection.silence(2_000)
        events = await spoken_until(
            session.events(), said("book a table", by_caller=True, final=True)
        )

    assert events == [
        SpeechStarted(by_caller=True),
        said("book a", by_caller=True, final=False),
        said(" table", by_caller=True, final=False),
        SpeechEnded(by_caller=True),
        said("book a table", by_caller=True, final=True),
    ]


async def test_a_caller_s_acknowledgement_over_the_assistant_does_not_split_its_sentence(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        start = connection.now_ms
        connection.silence(600)
        connection.emit(
            {
                "type": "session.output_transcript.delta",
                "delta": "the table is booked ",
                "start_ms": start,
                "end_ms": start + 500,
            }
        )
        connection.emit(
            {
                "type": "session.input_transcript.delta",
                "delta": "mm",
                "start_ms": start + 300,
                "end_ms": start + 400,
            }
        )
        connection.emit(
            {
                "type": "session.output_transcript.delta",
                "delta": "for eight",
                "start_ms": start + 500,
                "end_ms": start + 600,
            }
        )
        connection.silence(2_000)
        events = await spoken_until(
            session.events(), said("the table is booked for eight", by_caller=False, final=True)
        )

    finals = [event for event in events if isinstance(event, TranscriptProduced) and event.is_final]
    assert finals == [
        said("mm", by_caller=True, final=True),
        said("the table is booked for eight", by_caller=False, final=True),
    ]


async def test_words_still_unsettled_when_the_connection_drops_are_settled(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.caller_said("book a table")
        service.current.drop()
        events = session.events()
        assert await next_of(events, TranscriptProduced) == said(
            "book a table", by_caller=True, final=False
        )
        assert await next_of(events, TranscriptProduced) == said(
            "book a table", by_caller=True, final=True
        )


# -------------------------------------------------------------------------------- language


async def test_the_model_follows_the_caller_into_another_listed_language_and_back(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    provider = make_provider(languages=("en", "hi"))
    async with await connect(provider) as session:
        connection = service.current
        events = session.events()

        connection.caller_said(HINDI)
        connection.silence(2_000)
        await next_of(events, SpeechEnded)
        await service.wait_for_sent("session.thinking.append")
        assert connection.sent_of("session.thinking.append")[-1] == {
            "type": "session.thinking.append",
            "delegation_id": None,
            "content": language.switch("hi"),
        }

        connection.caller_said("Sorry, could we speak English please")
        connection.silence(2_000)
        await service.wait_for_sent("session.thinking.append", count=2)
        assert connection.sent_of("session.thinking.append")[-1]["content"] == language.switch("en")

        # Too short to be a change of language, and English is being spoken already.
        connection.caller_said("OK")
        connection.caller_said("Thank you so much for that", after_ms=1_600)
        connection.silence(2_000)
        final = said("Thank you so much for that", by_caller=True, final=True)
        await spoken_until(events, final)
        assert len(connection.sent_of("session.thinking.append")) == 2


async def test_a_language_the_deployment_does_not_list_is_not_switched_to(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.caller_said(HINDI)
        service.current.silence(2_000)
        await spoken_until(session.events(), said(HINDI, by_caller=True, final=True))
        assert service.current.sent_of("session.thinking.append") == []


async def test_a_replacement_session_carries_on_in_the_language_it_was_switched_to(
    make_provider: ProviderFactory, service: ScriptedGptLiveService
) -> None:
    provider = make_provider(languages=("en", "hi"))
    async with await connect(provider) as session:
        service.current.caller_said(HINDI)
        service.current.silence(2_000)
        await service.wait_for_sent("session.thinking.append")
        service.current.drop()
        await next_of(session.events(), SpeechEnded)
        await service.wait_for_sent("session.instructions.append", connection=2)
        assert service.current.appended_instructions() == [language.resumption("hi")]


# --------------------------------------------------------------------------------- context


async def test_only_the_lines_that_changed_are_added_to_the_instructions(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    before = "You answer calls.\nuser: not_asked"
    async with await connect(provider, context=before) as session:
        await session.update_context("You answer calls.\nuser: being_reached")
        await session.update_context("You answer calls.\nuser: being_reached")
        added = service.current.appended_instructions()[1:]
    assert len(added) == 1
    assert added[0].endswith("\nuser: being_reached")
    assert "You answer calls." not in added[0]
    assert len(service.connections) == 1


async def test_a_long_change_is_added_in_pieces_the_protocol_accepts(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        lines = [f"fact {number}: " + "x" * 90 for number in range(30)]
        await session.update_context("\n".join(lines))
        added = service.current.appended_instructions()[1:]
    assert len(added) > 1
    assert all(len(piece) <= APPEND_CHARACTERS for piece in added)
    assert "\n".join(added).count("fact ") == 30


async def test_a_change_made_while_a_replacement_starts_reaches_it(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider, context="user: not_asked") as session:
        service.starts = False
        service.current.drop()
        await service.wait_for_sent("session.start", connection=2)
        await session.update_context("user: on_the_call")
        service.current.emit({"type": "session.started"})
        await service.wait_for_sent("session.instructions.append", connection=2, count=2)
        replacement = service.current
        assert replacement.sent[0]["session"]["instructions"] == "user: not_asked"
        assert replacement.appended_instructions()[-1].endswith("\nuser: on_the_call")


async def test_a_delegation_is_told_to_carry_on_without_help(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider):
        service.current.delegate("item_example")
        await service.wait_for_sent("session.thinking.append")
        [thinking] = service.current.sent_of("session.thinking.append")
    assert thinking == {
        "type": "session.thinking.append",
        "delegation_id": "item_example",
        "content": NO_DELEGATE,
    }


# ---------------------------------------------------------------------------- interruption


async def test_interrupting_drops_the_rest_of_the_speech_until_the_caller_speaks(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(speech=2)
        events = session.events()
        await next_of(events, AudioProduced)
        await session.interrupt()
        connection.reply(speech=5, transcript="and another thing")
        connection.caller_said("stop")
        assert await next_of(events, SpeechEvent) == SpeechStarted(by_caller=True)
        assert await next_of(events, SpeechEvent) == said("stop", by_caller=True, final=False)


async def test_interrupted_speech_that_has_ended_lets_the_next_be_heard(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(speech=2)
        events = session.events()
        await next_of(events, AudioProduced)
        await session.interrupt()
        connection.reply(speech=2, silence=900, transcript="unheard")
        connection.reply(transcript="heard")
        assert await next_of(events, SpeechStarted) == SpeechStarted(by_caller=False)
        assert await next_of(events, TranscriptProduced) == said(
            "heard", by_caller=False, final=False
        )


# ---------------------------------------------------------------------------- reconnection


async def test_a_dropped_connection_is_replaced_and_told_what_it_missed(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    metrics: RecordingMetrics,
    sleep: RecordedSleep,
) -> None:
    async with await connect(provider, context="user: not_asked") as session:
        first = service.current
        await session.update_context("user: being_reached")
        first.caller_said("book a table")
        first.reply(transcript="for how many", silence=2_000)
        events = session.events()
        await spoken_until(events, said("for how many", by_caller=False, final=True))
        first.drop()
        await service.wait_for_sent("session.instructions.append", connection=2)

        second = service.current
        start = second.sent[0]["session"]
        assert start["instructions"] == "user: being_reached"
        assert start["audio"]["output"]["voice"] == "gleam"
        assert start["input"] == [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "book a table"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "for how many"}],
            },
        ]
        # Not greeted again.
        assert second.sent_types()[1:] == ["session.instructions.append"]
        assert second.appended_instructions() == [language.resumption("en")]

        # And the conversation carries on over the new session.
        await session.send_audio(TWENTY_MS_NARROWBAND)
        await next_of(events, AudioProduced)

    assert first.closed
    assert sleep.delays == [pytest.approx(0.375)]
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 1
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="connection") == 1


async def test_a_session_the_service_ends_for_its_own_reasons_is_replaced(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, metrics: RecordingMetrics
) -> None:
    async with await connect(provider):
        service.current.close_session("expired")
        await service.wait_for_sent("session.instructions.append", connection=2)
    assert metrics.observed(telemetry.SESSION_SECONDS) == [USAGE_SECONDS, USAGE_SECONDS]


async def test_a_session_the_service_ends_over_its_content_is_not_replaced(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.close_session("content", seconds=None)
        events = await remaining(session.events())
        with pytest.raises(ProviderError):
            await session.send_audio(TWENTY_MS_NARROWBAND)
    assert isinstance(events[-1], SessionFailed)
    assert not events[-1].retryable
    assert len(service.connections) == 1
    assert service.open_connections == 0


async def test_a_permanent_failure_ends_the_session_with_a_typed_error(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.drop(retryable=False)
        events = await remaining(session.events())
    assert events == [SessionFailed("dropped", retryable=False)]
    assert len(service.connections) == 1


async def test_reconnection_gives_up_after_its_attempts(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    metrics: RecordingMetrics,
) -> None:
    async with await connect(provider) as session:
        service.refuse_next(retryable=True, times=3)
        service.current.drop()
        events = await remaining(session.events())
    assert isinstance(events[-1], SessionFailed)
    assert "3 attempts" in events[-1].reason
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="failed") == 1


async def test_a_service_that_starts_and_drops_every_replacement_runs_out_of_attempts(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.answer_audio = False
        service.current.drop()
        for count in range(2, 5):
            await service.wait_for_sent("session.instructions.append", connection=count)
            service.current.drop()
        events = await remaining(session.events())
    assert isinstance(events[-1], SessionFailed)
    assert len(service.connections) == 4


async def test_sending_while_reconnecting_is_dropped_not_raised(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, sleep: RecordedSleep
) -> None:
    sleep.hold = asyncio.Event()
    async with await connect(provider) as session:
        service.current.drop()
        await sleep.entered.wait()
        await session.send_audio(TWENTY_MS_NARROWBAND)
        await session.update_context("user: on_the_call")
        sleep.hold.set()
        await service.wait_for_connection_count(2)
        await service.wait_for_sent("session.instructions.append", connection=2)
        assert service.current.sent[0]["session"]["instructions"] == "user: on_the_call"


async def test_a_send_that_fails_on_a_dying_connection_is_left_to_the_reader(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    async with await connect(provider) as session:
        service.current.closed = True
        await session.send_audio(TWENTY_MS_NARROWBAND)
        service.current.closed = False


# -------------------------------------------------------------------------- what is counted


async def test_malformed_unknown_and_refused_events_do_not_end_the_conversation(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, metrics: RecordingMetrics
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.emit({"type": "session.output_audio.delta", "delta": "not base64!"})
        connection.emit({"type": "session.input_transcript.delta", "delta": "no timing"})
        connection.emit({"type": "session.delegation.created"})
        connection.emit({"type": "session.usage.updated", "usage": {"seconds": 3}})
        connection.emit({"type": "session.started"})
        connection.emit({"type": "session.input_transcript.delta", "delta": " "})
        connection.emit({"type": "error", "error": {"type": "invalid_request_error"}})
        connection.caller_said("still there?")
        event = await next_of(session.events(), TranscriptProduced)
    assert event == said("still there?", by_caller=True, final=False)
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="malformed") == 3
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="service") == 1


async def test_latency_is_measured_to_speech_rather_than_to_silence(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    metrics: RecordingMetrics,
    clock: ManualClock,
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        events = session.events()
        clock.now += 0.5
        connection.silence(100)
        connection.reply(silence=2_000)
        await next_of(events, SpeechEnded)
        connection.caller_said("and tomorrow?")
        await spoken_until(events, said("and tomorrow?", by_caller=True, final=False))
        clock.now += 0.25
        connection.reply()
        await next_of(events, SpeechStarted)

    assert metrics.observed(telemetry.TIME_TO_FIRST_AUDIO) == pytest.approx([0.5])
    assert metrics.observed(telemetry.ROUND_TRIP) == pytest.approx([0.25])


# ------------------------------------------------------------------------------- resources


async def test_closing_finalises_the_session_and_records_its_voice_time(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, metrics: RecordingMetrics
) -> None:
    session = await connect(provider)
    await session.close()
    assert service.current.sent_types()[-1] == "session.close"
    assert metrics.observed(telemetry.SESSION_SECONDS) == [USAGE_SECONDS]
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_a_session_the_service_never_finalises_is_released_anyway(
    make_provider: ProviderFactory, service: ScriptedGptLiveService, metrics: RecordingMetrics
) -> None:
    service.finalises = False
    session = await connect(make_provider(close_timeout=0.05))
    await session.close()
    assert metrics.observed(telemetry.SESSION_SECONDS) == []
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_a_connection_that_ends_while_closing_is_not_replaced(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    service.finalises = False
    session = await connect(provider)
    closing = asyncio.create_task(session.close())
    await service.wait_for_sent("session.close")
    service.current.drop()
    await closing
    assert len(service.connections) == 1
    assert live_tasks() == set()


async def test_a_defect_in_the_reader_ends_the_stream_and_is_raised_on_close(
    provider: GptLiveSpeechProvider,
    service: ScriptedGptLiveService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_: ScriptedGptLiveConnection) -> None:
        raise RuntimeError("a connection that broke its own contract")

    session = await connect(provider)
    monkeypatch.setattr(ScriptedGptLiveConnection, "receive", broken)
    service.current.hang_up()

    events = await remaining(session.events())
    assert events == [SessionFailed("the session stopped unexpectedly", retryable=False)]
    with pytest.raises(RuntimeError):
        await session.close()
    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_twice_ends_the_stream_for_every_reader(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    session = await connect(provider)
    await session.close()
    await session.close()
    assert await remaining(session.events()) == []
    with pytest.raises(ProviderError):
        await session.update_context("too late")
    with pytest.raises(ProviderError):
        await session.interrupt()
    assert service.open_connections == 0


async def test_cancelling_the_consumer_mid_speech_leaves_nothing(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    heard = asyncio.Event()

    async def converse() -> None:
        async with await connect(provider) as session:
            await session.send_audio(TWENTY_MS_NARROWBAND)
            async for event in session.events():
                if isinstance(event, AudioProduced):
                    heard.set()

    service.speech_deltas = 50
    task = asyncio.create_task(converse())
    await heard.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_cancelling_during_reconnection_backoff_leaves_nothing(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService, sleep: RecordedSleep
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


async def test_a_timeout_leaves_nothing(
    provider: GptLiveSpeechProvider, service: ScriptedGptLiveService
) -> None:
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05), await connect(provider) as session:
            async for _ in session.events():
                pass

    assert live_tasks() == set()
    assert service.open_connections == 0


# ------------------------------------------------------------------------------- the offer


def test_it_declares_only_what_it_implements(provider: GptLiveSpeechProvider) -> None:
    capabilities = provider.capabilities
    assert provider.name == "gpt_live"
    assert capabilities.barge_in
    assert capabilities.context_updates_mid_session
    assert capabilities.reconnection
    assert capabilities.output_format == TELEPHONY_NARROWBAND


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": " "},
        {"audio_ceiling_seconds": 0},
        {"start_timeout": 0},
        {"close_timeout": 0},
        {"turn_gap_ms": 0},
    ],
)
def test_a_configuration_it_cannot_honour_is_refused_at_construction(
    service: ScriptedGptLiveService, metrics: RecordingMetrics, overrides: dict[str, object]
) -> None:
    options: dict[str, object] = {
        "model": "a-live-model",
        "languages": ("en",),
        "input_formats": (TELEPHONY_NARROWBAND,),
        "output_format": TELEPHONY_NARROWBAND,
        **overrides,
    }
    with pytest.raises(InvariantError):
        GptLiveSpeechProvider(service.open, metrics, **options)  # type: ignore[arg-type]
