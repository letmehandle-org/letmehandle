# ruff: noqa: T201 - a terminal tool whose output is the point
"""Talk to the configured speech service from a terminal, through the speech port (D-013)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import statistics
import sys
import threading
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import sounddevice  # type: ignore[import-untyped]

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.clock import SystemClock
from letmehandle.adapters.speech.websocket.connection import (
    ConnectionFailedError,
    ConnectionOpener,
    EventConnection,
)
from letmehandle.application.agent.prompts import load_prompts
from letmehandle.application.speech.conversation import Conversation, Transcript
from letmehandle.bootstrap import build_speech_provider
from letmehandle.config.settings import ConfigurationError, get_settings
from letmehandle.domain.errors import DomainError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioEncoding, AudioFormat, AudioFrame
from letmehandle.domain.ports.audio_io import AudioSink, AudioSource
from letmehandle.domain.ports.metrics import MetricsRecorder
from letmehandle.observability.metrics import checked_labels

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from letmehandle.domain.ports.speech import SpeechSession

# Audio per microphone callback, in milliseconds.
_FRAME_MS = 20
_SPEAKER_FORMAT = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)
# Unplayed seconds the speaker holds before writing waits, small so an interruption can undo it.
_MAX_BUFFERED_SECONDS = 0.3


class MicrophoneSource(AudioSource):
    """The default input device, as 16 kHz linear frames."""

    def __init__(self) -> None:
        self._frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        self.dropped = 0

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    async def frames(self) -> AsyncIterator[AudioFrame]:
        loop = asyncio.get_running_loop()

        def captured(data: Any, _frames: int, _time: Any, _status: Any) -> None:
            chunk = bytes(data)
            loop.call_soon_threadsafe(self._offer, chunk)

        blocksize = SPEECH_WIDEBAND.sample_rate_hz * _FRAME_MS // 1_000
        with sounddevice.RawInputStream(
            samplerate=SPEECH_WIDEBAND.sample_rate_hz,
            channels=1,
            dtype="int16",
            blocksize=blocksize,
            callback=captured,
        ):
            while True:
                yield AudioFrame(await self._frames.get(), SPEECH_WIDEBAND)

    def _offer(self, chunk: bytes) -> None:
        # When the conversation falls behind, the newest audio is dropped and counted.
        try:
            self._frames.put_nowait(chunk)
        except asyncio.QueueFull:
            self.dropped += 1


class SpeakerSink(AudioSink):
    """The default output device. Converts whatever arrives into what it plays."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._converters: dict[AudioFormat, AudioConverter] = {}
        self._stream = sounddevice.RawOutputStream(
            samplerate=_SPEAKER_FORMAT.sample_rate_hz,
            channels=1,
            dtype="int16",
            callback=self._play,
        )

    @property
    def format(self) -> AudioFormat:
        return _SPEAKER_FORMAT

    def __enter__(self) -> SpeakerSink:
        self._stream.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stream.stop()
        self._stream.close()

    async def write(self, frame: AudioFrame) -> None:
        converter = self._converters.get(frame.format)
        if converter is None:
            converter = self._converters[frame.format] = AudioConverter(frame.format, self.format)
        data = converter.convert(frame.data)
        limit = int(_MAX_BUFFERED_SECONDS * self.format.sample_rate_hz) * 2
        # Drained by the audio device's thread, which has no event loop, so the wait polls.
        while self._buffered() > limit:  # noqa: ASYNC110
            await asyncio.sleep(_FRAME_MS / 1_000)
        with self._lock:
            self._buffer.extend(data)

    async def discard(self) -> None:
        with self._lock:
            self._buffer.clear()
        for converter in self._converters.values():
            converter.reset()

    def _buffered(self) -> int:
        with self._lock:
            return len(self._buffer)

    def _play(self, out: Any, frames: int, _time: Any, _status: Any) -> None:
        wanted = frames * 2
        with self._lock:
            chunk = bytes(self._buffer[:wanted])
            del self._buffer[:wanted]
        out[: len(chunk)] = chunk
        # Silence for whatever the model has not produced yet, rather than the last buffer again.
        out[len(chunk) :] = b"\x00" * (wanted - len(chunk))


class SummaryRecorder(MetricsRecorder):
    """Keeps every measurement, to print a latency summary when the harness exits."""

    def __init__(self) -> None:
        self.observations: dict[str, list[float]] = defaultdict(list)
        self.counts: dict[str, int] = defaultdict(int)

    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        checked_labels(name, labels)
        self.observations[name].append(value)

    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        checked_labels(name, labels)
        self.counts[name] += 1

    def summary(self) -> str:
        lines = ["", "latency summary (seconds)"]
        for name, values in sorted(self.observations.items()):
            ordered = sorted(values)
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            lines.append(
                f"  {name}: n={len(values)} median={statistics.median(values):.3f} "
                f"p95={p95:.3f} max={ordered[-1]:.3f}"
            )
        for name, count in sorted(self.counts.items()):
            lines.append(f"  {name}: {count}")
        return "\n".join(lines)


class Severable:
    """Stands between the session and the network, so the harness can cut the line."""

    def __init__(self) -> None:
        self._live: list[_SeverableConnection] = []

    def wrap(self, opener: ConnectionOpener) -> ConnectionOpener:
        async def open_severable() -> EventConnection:
            connection = _SeverableConnection(await opener())
            self._live.append(connection)
            return connection

        return open_severable

    async def sever(self) -> None:
        for connection in self._live:
            await connection.sever()
        self._live.clear()


class _SeverableConnection:
    def __init__(self, inner: EventConnection) -> None:
        self._inner = inner
        self._severed = asyncio.Event()

    async def send(self, event: Mapping[str, Any]) -> None:
        await self._inner.send(event)

    async def receive(self) -> Mapping[str, Any] | None:
        receiving = asyncio.ensure_future(self._inner.receive())
        severed = asyncio.ensure_future(self._severed.wait())
        done, pending = await asyncio.wait(
            {receiving, severed}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if severed in done:
            raise ConnectionFailedError("severed by the harness", retryable=True)
        return receiving.result()

    async def close(self) -> None:
        await self._inner.close()

    async def sever(self) -> None:
        self._severed.set()
        await self._inner.close()


async def _commands(
    session: SpeechSession, sink: SpeakerSink, severable: Severable, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        line = (await asyncio.to_thread(sys.stdin.readline)).strip()
        if not line or line == "quit":
            stop.set()
            return
        verb, _, rest = line.partition(" ")
        try:
            if verb == "context" and rest:
                await session.update_context(rest)
                print("context updated")
            elif verb == "interrupt":
                # The session stops the model and the speaker drops what it still holds.
                await session.interrupt()
                await sink.discard()
                print("interrupted")
            elif verb == "disconnect":
                await severable.sever()
                print("connection dropped")
            else:
                print("commands: context <text> | interrupt | disconnect | quit")
        except DomainError as error:
            # A failed session refuses commands; the harness keeps running so the summary prints.
            print(f"not done: {error}")


async def _run(context: str, locale: str) -> None:
    settings = get_settings()
    recorder = SummaryRecorder()
    severable = Severable()
    provider = build_speech_provider(settings, metrics=recorder, wrap_connection=severable.wrap)
    transcript = Transcript()
    source = MicrophoneSource()
    stop = asyncio.Event()

    try:
        async with (
            await provider.connect(
                system_context=context,
                voice_id=settings.require_voice_catalogue()[1],
                greeting=load_prompts(locale).greeting(),
                locale=locale,
                input_format=SPEECH_WIDEBAND,
            ) as session,
            asyncio.TaskGroup() as group,
        ):
            with SpeakerSink() as sink:
                conversation = Conversation(
                    session=session,
                    source=source,
                    sink=sink,
                    transcript=transcript,
                    metrics=recorder,
                    clock=SystemClock(),
                )
                talking = group.create_task(conversation.run())
                talking.add_done_callback(lambda _task: stop.set())
                group.create_task(_commands(session, sink, severable, stop))
                await stop.wait()
                talking.cancel()
                print("ending: press return if the prompt is still waiting")
    finally:
        for turn in transcript.turns:
            print(f"{'caller' if turn.speaker_is_caller else 'model '}: {turn.text}")
        print(recorder.summary())
        if source.dropped:
            print(f"  microphone frames dropped: {source.dropped}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--context", default="You are a helpful assistant on a phone call.")
    parser.add_argument("--locale", default="en")
    arguments = parser.parse_args()
    try:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run(arguments.context, arguments.locale))
    except ConfigurationError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
