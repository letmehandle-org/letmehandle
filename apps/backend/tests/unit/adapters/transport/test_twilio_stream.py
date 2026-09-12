"""One leg's media: audio in, audio out, pacing, interruption and the end of it."""

from __future__ import annotations

import asyncio

import pytest

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.transport.twilio import stream as stream_module
from letmehandle.adapters.transport.twilio.stream import (
    INBOUND_QUEUE_FRAMES,
    PLAYBACK_LEAD_SECONDS,
    CallAudioSink,
    CallAudioSource,
    MediaStream,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND, AudioFrame
from tests.support.media_socket import MemoryMediaSocket


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Locator:
    def __init__(self, stream: MediaStream | None) -> None:
        self.stream = stream

    def current_stream(self) -> MediaStream | None:
        return self.stream


class Sleeps:
    def __init__(self) -> None:
        self.slept: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def connected(
    clock: Clock | None = None, sleeps: Sleeps | None = None
) -> tuple[MediaStream, MemoryMediaSocket]:
    stream = MediaStream(monotonic=clock or Clock(), sleep=sleeps or Sleeps())
    socket = MemoryMediaSocket()
    stream.attach(socket, "MZsim-1")
    return stream, socket


def narrowband(size: int = 160, byte: int = 0x55) -> AudioFrame:
    return AudioFrame(bytes([byte]) * size, TELEPHONY_NARROWBAND)


async def test_audio_from_the_call_comes_out_as_narrowband_frames_and_ends_with_the_stream() -> (
    None
):
    stream, _socket = connected()
    stream.receive(b"\x01\x02")
    stream.receive(b"")
    await stream.end()
    frames = [frame async for frame in stream.frames()]
    assert frames == [AudioFrame(b"\x01\x02", TELEPHONY_NARROWBAND)]


async def test_a_listener_that_falls_behind_loses_the_oldest_audio_not_the_newest() -> None:
    stream, _socket = connected()
    for index in range(INBOUND_QUEUE_FRAMES + 3):
        stream.receive(index.to_bytes(2, "big"))
    assert stream.dropped_frames == 3
    await stream.end()
    frames = [frame async for frame in stream.frames()]
    # One more was dropped to make room for the end.
    assert len(frames) == INBOUND_QUEUE_FRAMES - 1
    assert frames[-1].data == (INBOUND_QUEUE_FRAMES + 2).to_bytes(2, "big")


async def test_audio_arriving_after_the_end_is_ignored_and_ending_twice_is_safe() -> None:
    stream, socket = connected()
    await stream.end()
    await stream.end()
    stream.receive(b"\x01")
    assert socket.closes == 1
    assert stream.has_ended
    assert not stream.is_connected


async def test_the_assistants_audio_is_converted_at_this_edge_and_sent_as_media() -> None:
    stream, socket = connected()
    wideband = AudioFrame(b"\x00\x10" * 320, SPEECH_WIDEBAND)
    await stream.play(wideband)
    expected = AudioConverter(SPEECH_WIDEBAND, TELEPHONY_NARROWBAND).convert(wideband.data)
    assert socket.audio_sent() == expected
    assert socket.sent[0]["streamSid"] == "MZsim-1"


async def test_a_converter_is_kept_per_format_and_replaced_when_the_format_changes() -> None:
    stream, socket = connected()
    await stream.play(narrowband())
    await stream.play(AudioFrame(b"\x00\x00" * 320, SPEECH_WIDEBAND))
    assert len(socket.sent) == 2


async def test_a_frame_too_short_to_make_a_sample_sends_nothing() -> None:
    stream, socket = connected()
    await stream.play(AudioFrame(b"\x01", SPEECH_WIDEBAND))
    assert socket.sent == []


async def test_a_writer_waits_once_it_is_further_ahead_than_the_call_has_played() -> None:
    sleeps = Sleeps()
    stream, _socket = connected(Clock(), sleeps)
    # Two hundred milliseconds each: the first two are within the lead, the third is not.
    for _ in range(3):
        await stream.play(narrowband(1_600))
    assert sleeps.slept == [pytest.approx(0.6 - PLAYBACK_LEAD_SECONDS)]


async def test_discarding_clears_the_call_and_resets_the_pacing() -> None:
    sleeps = Sleeps()
    stream, socket = connected(Clock(), sleeps)
    slept = sleeps.slept
    await stream.discard()
    await stream.play(narrowband(8_000))
    await stream.discard()
    await stream.play(narrowband(1_600))
    assert socket.events_sent() == ["clear", "media", "clear", "media"]
    # Only the first second of audio made the writer wait; after the clear it starts afresh.
    assert slept == [pytest.approx(1.0 - PLAYBACK_LEAD_SECONDS)]


async def test_discarding_before_anything_connected_or_after_the_end_does_nothing() -> None:
    stream = MediaStream(monotonic=Clock())
    await stream.discard()
    connected_stream, socket = connected()
    await connected_stream.end()
    await connected_stream.discard()
    assert socket.sent == []


async def test_a_listening_only_leg_sends_nothing() -> None:
    stream = MediaStream(monotonic=Clock(), is_muted=lambda: True)
    socket = MemoryMediaSocket()
    stream.attach(socket, "MZsim-1")
    await stream.play(narrowband())
    assert socket.sent == []


async def test_a_writer_waits_for_the_stream_to_connect() -> None:
    stream = MediaStream(monotonic=Clock())
    socket = MemoryMediaSocket()
    playing = asyncio.create_task(stream.play(narrowband()))
    await asyncio.sleep(0)
    assert not playing.done()
    stream.attach(socket, "MZsim-2")
    await playing
    assert socket.events_sent() == ["media"]


async def test_a_stream_that_never_connects_is_a_retryable_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stream_module, "STREAM_CONNECT_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ProviderError, match="did not connect") as failure:
        await MediaStream(monotonic=Clock()).play(narrowband())
    assert failure.value.retryable


async def test_a_stream_that_ends_before_connecting_fails_the_writer_at_once() -> None:
    stream = MediaStream(monotonic=Clock())
    playing = asyncio.create_task(stream.play(narrowband()))
    await asyncio.sleep(0)
    await stream.end()
    with pytest.raises(ProviderError, match="has ended") as failure:
        await playing
    assert not failure.value.retryable


async def test_a_socket_that_closed_underneath_a_writer_is_a_final_failure() -> None:
    stream, socket = connected()
    socket.closed = True
    with pytest.raises(ProviderError, match="has ended"):
        await stream.play(narrowband())


class TestSourceAndSink:
    async def test_a_source_follows_the_current_stream_and_ends_when_there_is_none(self) -> None:
        stream, _socket = connected()
        locator = Locator(stream)
        source = CallAudioSource(locator)
        assert source.format == TELEPHONY_NARROWBAND
        stream.receive(b"\x09")
        await stream.end()
        assert [frame.data async for frame in source.frames()] == [b"\x09"]
        locator.stream = None
        assert [frame async for frame in source.frames()] == []

    async def test_a_sink_plays_onto_the_current_stream_and_discards_from_it(self) -> None:
        stream, socket = connected()
        locator = Locator(stream)
        sink = CallAudioSink(locator)
        assert sink.format == TELEPHONY_NARROWBAND
        await sink.write(narrowband())
        await sink.discard()
        assert socket.events_sent() == ["media", "clear"]

    async def test_a_sink_with_no_assistant_on_the_call_refuses_to_play_and_discards_nothing(
        self,
    ) -> None:
        sink = CallAudioSink(Locator(None))
        await sink.discard()
        with pytest.raises(ProviderError, match="no assistant"):
            await sink.write(narrowband())
