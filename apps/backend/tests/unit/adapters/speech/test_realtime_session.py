"""A realtime session against a service that behaves like one.

Resources are proven by counting what is left — tasks still alive, connections still open — and
never by looking at a flag the session sets on itself.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.speech.realtime.provider import RealtimeSpeechProvider
from letmehandle.adapters.speech.session_support import telemetry
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
    SpeechEnded,
    SpeechEvent,
    SpeechSession,
    SpeechStarted,
    TranscriptProduced,
)
from tests.support.scripted_realtime_connection import (
    REPLY_TRANSCRIPT,
    ScriptedRealtimeConnection,
    ScriptedRealtimeService,
    audio_delta,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.support.recording_metrics import RecordingMetrics
    from tests.unit.adapters.speech.conftest import ManualClock, ProviderFactory, RecordedSleep

TWENTY_MS_WIDEBAND = AudioFrame(bytes(640), SPEECH_WIDEBAND)


async def connect(
    provider: RealtimeSpeechProvider, input_format: AudioFormat = SPEECH_WIDEBAND
) -> SpeechSession:
    return await provider.connect(
        system_context="answer for someone", voice_id="calm", locale="en", input_format=input_format
    )


async def take(events: AsyncIterator[SpeechEvent], count: int) -> list[SpeechEvent]:
    async with asyncio.timeout(2):
        return [await anext(events) for _ in range(count)]


async def next_of[E: SpeechEvent](events: AsyncIterator[SpeechEvent], kind: type[E]) -> E:
    async with asyncio.timeout(2):
        async for event in events:
            if isinstance(event, kind):
                return event
    raise AssertionError(f"the stream ended without a {kind.__name__}")


async def remaining(events: AsyncIterator[SpeechEvent]) -> list[SpeechEvent]:
    async with asyncio.timeout(2):
        return [event async for event in events]


def live_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


# ------------------------------------------------------------------------------- connecting


async def test_connecting_configures_the_session_before_anything_else(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider):
        first = service.current.sent[0]
        assert first["type"] == "session.update"
        assert first["session"]["instructions"] == "answer for someone"
        assert first["session"]["audio"]["output"]["voice"] == "calm"


async def test_transcription_is_configured_in_the_session_s_language(
    make_provider: ProviderFactory, service: ScriptedRealtimeService
) -> None:
    provider = make_provider(transcription_model="a-transcriber")
    async with await provider.connect(
        system_context="c", voice_id="calm", locale="en-GB", input_format=SPEECH_WIDEBAND
    ):
        transcription = service.current.sent[0]["session"]["audio"]["input"]["transcription"]
        assert transcription == {"model": "a-transcriber", "language": "en"}


@pytest.mark.parametrize("retryable", [True, False])
async def test_a_refused_connection_is_a_typed_error_that_says_whether_to_retry(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, retryable: bool
) -> None:
    service.refuse_next(retryable=retryable)
    with pytest.raises(ProviderError) as raised:
        await connect(provider)
    assert raised.value.retryable is retryable
    assert live_tasks() == set()


async def test_a_connection_that_closes_while_being_configured_is_released(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    service.stalled = asyncio.Event()
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_connection_count(1)
    await service.current.close()
    service.stalled.set()

    with pytest.raises(ProviderError) as raised:
        await connecting
    assert not raised.value.retryable
    assert service.open_connections == 0


async def test_cancelling_a_connect_part_way_through_configuring_releases_the_connection(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    service.stalled = asyncio.Event()
    connecting = asyncio.create_task(connect(provider))
    await service.wait_for_connection_count(1)

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
    provider: RealtimeSpeechProvider,
    service: ScriptedRealtimeService,
    locale: str,
    input_format: AudioFormat,
    voice: str,
    refusal: type[Exception],
) -> None:
    with pytest.raises(refusal):
        await provider.connect(
            system_context="c", voice_id=voice, locale=locale, input_format=input_format
        )
    # Refused before a connection was ever opened.
    assert service.connections == []


# -------------------------------------------------------------------------------- audio in


async def test_caller_audio_reaches_the_service_as_wire_audio(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    service.answer_audio = False
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        appended = base64.b64decode(service.current.sent[-1]["audio"])
    # Twenty milliseconds at 16 kHz is 320 samples, and at 24 kHz it is 480 — less the one the
    # interpolator holds back until the next frame arrives.
    assert abs(len(appended) // 2 - 480) <= 1


async def test_a_phone_call_s_audio_is_converted_too(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    service.answer_audio = False
    async with await connect(provider, TELEPHONY_NARROWBAND) as session:
        await session.send_audio(AudioFrame(bytes([0xFF] * 160), TELEPHONY_NARROWBAND))
        appended = base64.b64decode(service.current.sent[-1]["audio"])
    assert set(appended) == {0}


async def test_a_frame_in_a_format_the_session_was_not_opened_for_is_refused(
    provider: RealtimeSpeechProvider,
) -> None:
    async with await connect(provider) as session:
        with pytest.raises(ProviderError):
            await session.send_audio(AudioFrame(bytes(160), TELEPHONY_NARROWBAND))


async def test_audio_too_short_to_make_a_sample_is_held_rather_than_sent_empty(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    service.answer_audio = False
    async with await connect(provider) as session:
        await session.send_audio(AudioFrame(b"\x01", SPEECH_WIDEBAND))
        assert service.current.sent_types() == ["session.update"]


# ------------------------------------------------------------------------------- audio out


async def test_a_reply_arrives_as_speech_audio_and_words(
    provider: RealtimeSpeechProvider,
) -> None:
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        events = await take(session.events(), 7)

    assert events[0] == SpeechStarted(by_caller=False)
    audio = [event for event in events[1:4] if isinstance(event, AudioProduced)]
    assert len(audio) == 3
    assert all(each.frame.format == SPEECH_WIDEBAND for each in audio)
    assert events[4:] == [
        TranscriptProduced(REPLY_TRANSCRIPT, speaker_is_caller=False, is_final=False),
        TranscriptProduced(REPLY_TRANSCRIPT, speaker_is_caller=False, is_final=True),
        SpeechEnded(by_caller=False),
    ]


async def test_model_audio_is_emitted_in_the_declared_output_format(
    make_provider: ProviderFactory,
) -> None:
    provider = make_provider(output_format=TELEPHONY_NARROWBAND)
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        produced = await next_of(session.events(), AudioProduced)
    assert produced.frame.format == TELEPHONY_NARROWBAND
    # Ten milliseconds of wire audio is eighty narrowband samples, one byte each.
    assert abs(len(produced.frame.data) - 80) <= 1


async def test_audio_split_mid_sample_is_joined_rather_than_emitted_empty(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.emit({"type": "response.created", "response": {"id": "resp_1"}})
        connection.emit(audio_delta("resp_1", "item_1", size=1))
        connection.emit(audio_delta("resp_1", "item_1", size=479))
        events = session.events()
        assert await anext(events) == SpeechStarted(by_caller=False)
        produced = await anext(events)
    # One frame, from both pieces together: nothing empty was emitted for the half sample.
    assert isinstance(produced, AudioProduced)
    assert abs(len(produced.frame.data) - 320) <= 2


async def test_a_response_without_audio_is_remembered_but_not_spoken(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        first = service.current
        first.reply(deltas=0, transcript="noted")
        first.caller_said("thanks")
        events = await take(session.events(), 4)
        first.drop()
        await service.wait_for_sent("session.update", connection=2)

    assert not any(isinstance(event, SpeechStarted | SpeechEnded) for event in events)
    restored = [item["item"]["content"][0]["text"] for item in service.current.sent[1:]]
    assert restored == ["noted", "thanks"]


async def test_a_response_that_did_not_complete_is_not_remembered(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        first = service.current
        first.emit({"type": "response.created", "response": {"id": "resp_1"}})
        first.emit({"type": "response.output_audio_transcript.done", "transcript": "half of"})
        first.emit({"type": "response.done", "response": {"id": "resp_1", "status": "incomplete"}})
        first.caller_said("hello?")
        await next_of(session.events(), TranscriptProduced)
        first.drop()
        await service.wait_for_sent("session.update", connection=2)

    restored = [item["item"]["content"][0]["text"] for item in service.current.sent[1:]]
    assert restored == ["hello?"]


async def test_the_caller_s_speech_and_words_are_reported(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        service.current.caller_starts_speaking()
        service.current.caller_said("is anyone there")
        service.current.caller_stops_speaking()
        events = await take(session.events(), 4)

    assert events == [
        SpeechStarted(by_caller=True),
        TranscriptProduced("is anyone there", speaker_is_caller=True, is_final=False),
        TranscriptProduced("is anyone there", speaker_is_caller=True, is_final=True),
        SpeechEnded(by_caller=True),
    ]


async def test_a_consumer_that_stops_taking_audio_stops_reading_at_the_ceiling(
    make_provider: ProviderFactory, service: ScriptedRealtimeService
) -> None:
    # Four ten-millisecond pieces fill four hundredths of a second, so the fifth waits for room
    # and everything behind it waits with it.
    provider = make_provider(audio_ceiling_seconds=0.04)
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=40)
        # Give the reader every chance to run ahead of a consumer that is not reading.
        for _ in range(50):
            await asyncio.sleep(0)
        stalled_at = connection.pending

        # The rest of the reply is still with the service. Read: its start, the four pieces that
        # fit, and the one in hand waiting for room.
        assert stalled_at == (1 + 40 + 3) - (1 + 4 + 1)

        events = session.events()
        await take(events, 20)
        assert connection.pending < stalled_at


# ---------------------------------------------------------------------------- interruption


async def test_interrupting_tells_the_service_how_much_was_heard_and_drops_the_rest(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=10)
        events = session.events()
        await next_of(events, AudioProduced)
        await anext(events)
        # Two ten-millisecond deltas taken by the consumer; the other eight were queued.
        await session.interrupt()

        truncate = connection.sent[-1]
        assert connection.sent_types()[-2:] == ["response.cancel", "conversation.item.truncate"]
        assert truncate["item_id"] == "item_1"
        assert truncate["audio_end_ms"] == 20

        connection.caller_said("wait")
        after = await anext(events)
    # The first thing out after the interruption is the caller, not the rest of the reply.
    assert after == TranscriptProduced("wait", speaker_is_caller=True, is_final=False)


async def test_interrupting_a_response_still_in_progress_measures_the_silence(
    make_provider: ProviderFactory,
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    clock: ManualClock,
) -> None:
    provider = make_provider(audio_ceiling_seconds=0.02)
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=10)
        events = session.events()
        await next_of(events, AudioProduced)

        await session.interrupt()
        # The reply is still in progress, so silence is the service confirming the cancel.
        assert metrics.observed(telemetry.INTERRUPTION_TO_SILENCE) == []
        clock.now += 0.25
        connection.caller_said("wait")
        await next_of(events, TranscriptProduced)

    assert metrics.observed(telemetry.INTERRUPTION_TO_SILENCE) == pytest.approx([0.25])


async def test_interruption_keeps_what_the_caller_said(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.caller_said("one thing")
        connection.reply(deltas=5)
        connection.caller_said("and another")
        await service.wait_until_delivered()
        await session.interrupt()
        events = await take(session.events(), 4)

    # The model's reply is gone; the caller's words are not, because the caller did say them.
    assert events == [
        TranscriptProduced("one thing", speaker_is_caller=True, is_final=False),
        TranscriptProduced("one thing", speaker_is_caller=True, is_final=True),
        TranscriptProduced("and another", speaker_is_caller=True, is_final=False),
        TranscriptProduced("and another", speaker_is_caller=True, is_final=True),
    ]


async def test_words_the_service_sent_before_it_heard_the_cancel_are_dropped(
    make_provider: ProviderFactory, service: ScriptedRealtimeService
) -> None:
    async with await connect(make_provider(audio_ceiling_seconds=0.02)) as session:
        connection = service.current
        connection.reply(deltas=4)
        events = session.events()
        await next_of(events, AudioProduced)
        await session.interrupt()
        connection.emit({"type": "response.output_audio_transcript.delta", "delta": "late"})
        connection.caller_said("go on")
        assert await anext(events) == TranscriptProduced(
            "go on", speaker_is_caller=True, is_final=False
        )


async def test_the_service_hearing_the_caller_interrupts_the_model_by_itself(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=5)
        connection.caller_starts_speaking()
        await service.wait_for_sent("conversation.item.truncate")

        first = await anext(session.events())

    # Everything the model had queued is gone; nothing of it was heard.
    assert first == SpeechStarted(by_caller=True)
    assert connection.sent[-1]["audio_end_ms"] == 0


async def test_the_caller_speaking_is_acted_on_while_the_consumer_is_behind(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    # A consumer playing through a speaker takes audio at the speed of speech, and the service
    # sends a ten-second reply in a moment. The caller talking over it must not wait for the
    # speaker to work through the reply first.
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=1_000)
        events = session.events()
        await next_of(events, AudioProduced)

        # Still playing the first ten milliseconds when the caller speaks.
        connection.caller_starts_speaking()
        await service.wait_for_sent("conversation.item.truncate")

        assert connection.sent_types()[-2:] == ["response.cancel", "conversation.item.truncate"]
        assert connection.sent[-1]["audio_end_ms"] == 10
        # The rest of the reply is gone, and the next thing the consumer takes is the caller.
        assert await anext(events) == SpeechStarted(by_caller=True)


async def test_the_caller_speaking_over_silence_interrupts_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, metrics: RecordingMetrics
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.caller_starts_speaking()
        assert await anext(session.events()) == SpeechStarted(by_caller=True)
        assert "response.cancel" not in connection.sent_types()
    assert metrics.observed(telemetry.INTERRUPTION_TO_SILENCE) == []


async def test_interrupting_when_nothing_is_playing_sends_only_a_cancel(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, metrics: RecordingMetrics
) -> None:
    async with await connect(provider) as session:
        await session.interrupt()
        service.current.caller_said("hello")
        await next_of(session.events(), TranscriptProduced)
        assert service.current.sent_types()[-1] == "response.cancel"
    # The service's refusal to cancel nothing is expected, not an error.
    assert metrics.counted(telemetry.STREAM_ERRORS) == 0


async def test_audio_the_service_sent_before_it_heard_the_cancel_is_dropped(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.reply(deltas=1)
        events = session.events()
        await next_of(events, AudioProduced)
        await session.interrupt()
        connection.emit(audio_delta("resp_1", "item_1"))
        connection.caller_said("go on")
        assert await anext(events) == TranscriptProduced(
            "go on", speaker_is_caller=True, is_final=False
        )


# --------------------------------------------------------------------------------- context


async def test_context_changes_without_a_new_connection(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        await session.update_context("the user has joined the call")
    assert len(service.connections) == 1
    assert service.current.sent[-1] == {
        "type": "session.update",
        "session": {"type": "realtime", "instructions": "the user has joined the call"},
    }


# ---------------------------------------------------------------------------- reconnection


async def test_a_dropped_connection_is_replaced_and_told_what_it_missed(
    provider: RealtimeSpeechProvider,
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    sleep: RecordedSleep,
) -> None:
    async with await connect(provider) as session:
        first = service.current
        await session.update_context("the user has joined")
        first.caller_said("book a table")
        first.reply(transcript="for how many")
        first.drop()
        events = session.events()
        await next_of(events, SpeechEnded)
        await service.wait_for_sent("session.update", connection=2)

        second = service.current
        assert second.sent[0]["session"]["instructions"] == "the user has joined"
        assert second.sent[0]["session"]["audio"]["output"]["voice"] == "calm"
        assert [item["item"]["role"] for item in second.sent[1:]] == ["user", "assistant"]
        assert [item["item"]["content"][0]["text"] for item in second.sent[1:]] == [
            "book a table",
            "for how many",
        ]

        # And the conversation carries on over the new connection.
        await session.send_audio(TWENTY_MS_WIDEBAND)
        await next_of(events, AudioProduced)

    assert first.closed
    assert sleep.delays == [pytest.approx(0.375)]
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 1
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="connection") == 1


async def test_the_service_hanging_up_is_reconnected_through(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider):
        service.current.hang_up()
        await service.wait_for_sent("session.update", connection=2)
    assert service.open_connections == 0


async def test_a_reply_that_was_cut_off_is_not_remembered_as_said(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        first = service.current
        first.reply(deltas=5, transcript="let me tell you")
        events = session.events()
        await next_of(events, AudioProduced)
        await session.interrupt()
        first.caller_said("stop")
        first.drop()
        await service.wait_for_sent("session.update", connection=2)

    restored = [item["item"]["content"][0]["text"] for item in service.current.sent[1:]]
    assert restored == ["stop"]


async def test_interrupting_after_a_reply_was_fully_heard_keeps_it(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        first = service.current
        first.reply(transcript="your table is booked")
        await next_of(session.events(), SpeechEnded)
        await session.interrupt()
        # Everything was heard, so there is nothing to truncate.
        assert first.sent_types()[-1] == "response.cancel"
        first.drop()
        await service.wait_for_sent("session.update", connection=2)

    restored = [item["item"]["content"][0]["text"] for item in service.current.sent[1:]]
    assert restored == ["your table is booked"]


async def test_a_reply_that_was_in_flight_when_the_connection_dropped_is_not_remembered(
    make_provider: ProviderFactory, service: ScriptedRealtimeService
) -> None:
    async with await connect(make_provider()):
        first = service.current
        first.emit({"type": "response.created", "response": {"id": "resp_9"}})
        first.emit({"type": "response.output_audio_transcript.done", "transcript": "half"})
        first.drop()
        await service.wait_for_sent("session.update", connection=2)
    assert service.current.sent_types() == ["session.update"]


async def test_what_is_replayed_is_bounded(
    make_provider: ProviderFactory, service: ScriptedRealtimeService
) -> None:
    async with await connect(make_provider(history_turns=1)):
        first = service.current
        first.caller_said("an old thing")
        first.caller_said("the latest thing")
        first.drop()
        await service.wait_for_sent("session.update", connection=2)
    restored = [item["item"]["content"][0]["text"] for item in service.current.sent[1:]]
    assert restored == ["the latest thing"]


async def test_a_permanent_failure_ends_the_session_with_a_typed_error(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, sleep: RecordedSleep
) -> None:
    async with await connect(provider) as session:
        service.current.drop(retryable=False)
        events = await remaining(session.events())

        assert events == [SessionFailed("dropped", retryable=False)]
        with pytest.raises(ProviderError) as raised:
            await session.send_audio(TWENTY_MS_WIDEBAND)
        assert not raised.value.retryable
        with pytest.raises(ProviderError):
            await session.update_context("anything")
        with pytest.raises(ProviderError):
            await session.interrupt()
        # Nothing was retried, and nothing is left open.
        assert sleep.delays == []
        assert len(service.connections) == 1
        assert service.open_connections == 0


async def test_reconnection_gives_up_after_its_attempts(
    make_provider: ProviderFactory,
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    sleep: RecordedSleep,
) -> None:
    async with await connect(make_provider(max_attempts=3)) as session:
        service.refuse_next(retryable=True, times=3)
        service.current.drop()
        events = await remaining(session.events())

    assert len(events) == 1
    assert isinstance(events[0], SessionFailed)
    # Exponential, jittered at the midpoint by the injected draw, and capped at the ceiling.
    assert sleep.delays == pytest.approx([0.375, 0.75, 1.5])
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="failed") == 1
    assert service.open_connections == 0


@pytest.mark.parametrize(
    "last_words",
    [
        [],
        # A refusal is not the service working either, nor is an event this adapter ignores.
        [{"type": "error", "error": {"code": "insufficient_quota"}}, {"type": "rate_limits"}],
    ],
)
async def test_a_service_that_accepts_and_drops_every_replacement_runs_out_of_attempts(
    provider: RealtimeSpeechProvider,
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    last_words: list[dict[str, object]],
) -> None:
    async with await connect(provider) as session:
        # Far more than three: a session that never runs out is still reconnecting afterwards.
        service.hang_ups_after_configuring = 10
        service.last_words = last_words
        service.current.hang_up()
        (failed,) = await remaining(session.events())

    # Each replacement was accepted and acknowledged its configuration, and none did anything
    # else: three attempts, not one after another for as long as the service keeps answering.
    assert failed == SessionFailed("the connection could not be restored in 3 attempts", False)
    assert len(service.connections) == 1 + 3
    assert metrics.counted(telemetry.RECONNECTIONS, outcome="succeeded") == 3
    assert service.open_connections == 0


async def test_a_replacement_that_delivers_something_earns_back_its_attempts(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        events = session.events()
        for count in range(2, 7):
            service.current.drop()
            await service.wait_for_sent("session.update", connection=count)
            service.current.caller_said(f"still here {count}")
            assert await take(events, 2) == [
                TranscriptProduced(f"still here {count}", speaker_is_caller=True, is_final=False),
                TranscriptProduced(f"still here {count}", speaker_is_caller=True, is_final=True),
            ]

    assert len(service.connections) == 6


async def test_a_refusal_that_will_not_change_stops_reconnecting_at_once(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, sleep: RecordedSleep
) -> None:
    async with await connect(provider) as session:
        service.refuse_next(retryable=True)
        service.refuse_next(retryable=False)
        service.current.drop()
        events = await remaining(session.events())
    assert events == [SessionFailed("refused", retryable=False)]
    assert len(sleep.delays) == 2


async def test_sending_while_reconnecting_is_dropped_not_raised(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, sleep: RecordedSleep
) -> None:
    sleep.hold = asyncio.Event()
    async with await connect(provider) as session:
        service.current.drop()
        await sleep.entered.wait()

        # Audio spoken into an outage is gone; the caller is not made to wait for the network.
        await session.send_audio(TWENTY_MS_WIDEBAND)
        await session.update_context("changed during the outage")
        sleep.hold.set()
        await service.wait_for_sent("session.update", connection=2)

        assert service.current.sent[0]["session"]["instructions"] == "changed during the outage"


async def test_a_send_that_fails_on_a_dying_connection_is_left_to_the_reader(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        dying = service.current
        dying.drop()
        await dying.close()
        await session.send_audio(TWENTY_MS_WIDEBAND)
        await service.wait_for_sent("session.update", connection=2)
    assert "input_audio_buffer.append" not in dying.sent_types()


# ------------------------------------------------------------------------ what gets counted


async def test_a_malformed_or_unknown_event_does_not_end_the_conversation(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, metrics: RecordingMetrics
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        connection.emit({"type": "response.created"})
        connection.emit({"type": "something.new", "payload": 1})
        connection.emit({"type": "error", "error": {"code": "invalid_value"}})
        connection.caller_said("still here")
        assert isinstance(await anext(session.events()), TranscriptProduced)
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="malformed") == 1
    assert metrics.counted(telemetry.STREAM_ERRORS, kind="service") == 1


async def test_latency_is_measured_from_the_injected_clock(
    provider: RealtimeSpeechProvider,
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    clock: ManualClock,
) -> None:
    async with await connect(provider) as session:
        connection = service.current
        events = session.events()
        clock.now += 0.5
        connection.caller_stops_speaking()
        await next_of(events, SpeechEnded)
        clock.now += 0.2
        connection.reply()
        await next_of(events, AudioProduced)

    assert metrics.observed(telemetry.TIME_TO_FIRST_AUDIO) == pytest.approx([0.7])
    assert metrics.observed(telemetry.ROUND_TRIP) == pytest.approx([0.2])
    # Dimensions only: never words, never an identifier.
    assert all(set(labels) <= {"provider", "kind", "outcome"} for labels in metrics.all_labels())


# ------------------------------------------------------------------------------- resources


async def test_a_defect_in_the_reader_ends_the_stream_and_is_raised_on_close(
    provider: RealtimeSpeechProvider,
    service: ScriptedRealtimeService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(_: ScriptedRealtimeConnection) -> None:
        raise RuntimeError("a connection that broke its own contract")

    session = await connect(provider)
    monkeypatch.setattr(ScriptedRealtimeConnection, "receive", broken)
    service.current.hang_up()

    events = await remaining(session.events())
    assert events == [SessionFailed("the session stopped unexpectedly", retryable=False)]
    with pytest.raises(RuntimeError):
        await session.close()
    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_twice_ends_the_stream_for_every_reader(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    session = await connect(provider)
    await session.close()
    await session.close()
    assert await remaining(session.events()) == []
    assert await remaining(session.events()) == []
    assert service.open_connections == 0
    assert live_tasks() == set()


async def test_cancelling_the_consumer_before_the_first_event_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async def converse() -> None:
        async with await connect(provider) as session:
            async for _ in session.events():
                pass

    task = asyncio.create_task(converse())
    await service.wait_for_sent("session.update")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_cancelling_the_consumer_mid_utterance_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    heard = asyncio.Event()

    async def converse() -> None:
        async with await connect(provider) as session:
            await session.send_audio(TWENTY_MS_WIDEBAND)
            async for event in session.events():
                if isinstance(event, AudioProduced):
                    heard.set()

    service.deltas_per_reply = 50
    task = asyncio.create_task(converse())
    await heard.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_cancelling_during_reconnection_backoff_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService, sleep: RecordedSleep
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


async def test_cancelling_while_a_replacement_is_being_configured_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async def converse() -> None:
        async with await connect(provider) as session:
            service.stalled = asyncio.Event()
            service.current.drop()
            async for _ in session.events():
                pass

    task = asyncio.create_task(converse())
    await service.wait_for_connection_count(2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_a_timeout_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0), await connect(provider) as session:
            async for _ in session.events():
                pass

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_a_failure_leaves_nothing(
    provider: RealtimeSpeechProvider, service: ScriptedRealtimeService
) -> None:
    async with await connect(provider) as session:
        service.current.drop(retryable=False)
        await remaining(session.events())

    assert live_tasks() == set()
    assert service.open_connections == 0


async def test_closing_while_the_consumer_waits_ends_its_iteration(
    provider: RealtimeSpeechProvider,
) -> None:
    session = await connect(provider)
    consumer = asyncio.create_task(remaining(session.events()))
    await asyncio.sleep(0)
    await session.close()
    assert await consumer == []


# ------------------------------------------------------------------------------- provider


def test_it_declares_only_what_it_implements(provider: RealtimeSpeechProvider) -> None:
    capabilities = provider.capabilities
    assert provider.name == "realtime"
    assert capabilities.barge_in
    assert capabilities.context_updates_mid_session
    assert capabilities.reconnection
    assert capabilities.languages == ("en",)
    assert capabilities.output_format == SPEECH_WIDEBAND


STEREO = AudioFormat(AudioEncoding.PCM_S16LE, 16_000, channels=2)
OPUS = AudioFormat(AudioEncoding.OPUS, 48_000)


@pytest.mark.parametrize(
    ("languages", "input_formats", "output_format", "ceiling"),
    [
        ((), (SPEECH_WIDEBAND,), SPEECH_WIDEBAND, 8),
        (("en",), (), SPEECH_WIDEBAND, 8),
        (("en",), (SPEECH_WIDEBAND, OPUS), SPEECH_WIDEBAND, 8),
        (("en",), (SPEECH_WIDEBAND,), STEREO, 8),
        (("en",), (SPEECH_WIDEBAND,), SPEECH_WIDEBAND, 0),
    ],
)
def test_a_configuration_it_cannot_honour_is_refused_at_construction(
    service: ScriptedRealtimeService,
    metrics: RecordingMetrics,
    languages: tuple[str, ...],
    input_formats: tuple[AudioFormat, ...],
    output_format: AudioFormat,
    ceiling: float,
) -> None:
    # Refused when the application starts, not on the first call that happens to need it.
    with pytest.raises(InvariantError):
        RealtimeSpeechProvider(
            service.open,
            metrics,
            languages=languages,
            input_formats=input_formats,
            output_format=output_format,
            audio_ceiling_seconds=ceiling,
        )


async def test_the_defaults_are_usable(
    service: ScriptedRealtimeService, metrics: RecordingMetrics
) -> None:
    provider = RealtimeSpeechProvider(
        service.open,
        metrics,
        languages=("en",),
        input_formats=(SPEECH_WIDEBAND,),
        output_format=SPEECH_WIDEBAND,
    )
    async with await connect(provider) as session:
        await session.send_audio(TWENTY_MS_WIDEBAND)
        assert isinstance(await next_of(session.events(), AudioProduced), AudioProduced)
