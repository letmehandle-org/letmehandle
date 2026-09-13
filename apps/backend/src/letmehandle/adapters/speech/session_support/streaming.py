"""The lifecycle shared by every speech session held over one replaceable event connection."""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.adapters.audio.conversion import AudioConverter
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.adapters.speech.session_support.outbox import Outbox
from letmehandle.adapters.speech.session_support.reconnect import ReconnectBudget
from letmehandle.adapters.speech.session_support.recovery import (
    is_retryable,
    receive,
    replace_connection,
)
from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import (
    ConnectionFailedError,
    EventConnectionError,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.ports.speech import SessionFailed, SpeechSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.reconnect import ReconnectPolicy
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )
    from letmehandle.domain.models.audio import AudioFormat, AudioFrame
    from letmehandle.domain.ports.speech import SpeechEvent


@dataclass(frozen=True, slots=True)
class SessionSetup:
    """What a session is built from that is not state it accumulates."""

    provider: str
    opener: ConnectionOpener
    input_format: AudioFormat
    output_format: AudioFormat
    reconnect: ReconnectPolicy
    audio_ceiling_seconds: float
    timekeeping: Timekeeping


class StreamingSpeechSession[Signal](SpeechSession):
    """A `SpeechSession` whose protocol says what its events mean and what a connection is told."""

    def __init__(
        self, setup: SessionSetup, telemetry: SessionTelemetry, *, wire_format: AudioFormat
    ) -> None:
        self._setup = setup
        self._telemetry = telemetry
        self._outbox = Outbox(setup.audio_ceiling_seconds)
        self._inbound = AudioConverter(setup.input_format, wire_format)
        self._outbound = AudioConverter(wire_format, setup.output_format)
        self._connection: EventConnection | None = None
        # The reader once started: a list, so closing an unstarted session is the same code.
        self._readers: list[asyncio.Task[None]] = []
        # False before the first connection is ready, while one is replaced, and after the end.
        self._live = False
        self._closed = False
        self._failure: str | None = None
        self._budget = ReconnectBudget()
        self._unproven = False

    # ------------------------------------------------------------------------------ protocol

    @abstractmethod
    def _audio_event(self, audio: bytes) -> Event:
        """The event carrying caller audio already in the wire format."""

    @abstractmethod
    def _parse_event(self, event: Event) -> Signal | None:
        """What an event means, `None` when unused; raises `MalformedEventError`."""

    @abstractmethod
    async def _introduce(self, connection: EventConnection) -> None:
        """Tell a newly opened connection everything it must know before it is live."""

    @abstractmethod
    async def _catch_up(self) -> None:
        """Send what changed while no connection could hear it."""

    @abstractmethod
    async def _handle(self, signal: Signal) -> None:
        """Act on one signal from the service."""

    @abstractmethod
    async def _forget_connection(self) -> None:
        """Let go of whatever state belonged to a connection that has just been lost."""

    def _proves(self, signal: Signal) -> bool:
        """Whether `signal` shows a replacement connection works rather than refusing it."""
        del signal
        return True

    async def _finish(self) -> None:
        """Bring the session to an end with the service before its connection is closed."""

    # ------------------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Open the first connection and begin reading it; a failure raises `ProviderError`."""
        try:
            await self._open()
        except EventConnectionError as error:
            await self._drop_connection()
            raise ProviderError(
                self._setup.provider, str(error), retryable=is_retryable(error)
            ) from error
        except BaseException:
            await self._drop_connection()
            raise
        await self._go_live()
        self._telemetry.opened()
        self._readers.append(
            asyncio.create_task(self._read(), name=f"{self._setup.provider}-reader")
        )

    async def close(self) -> None:
        """Stop reading, close the connection and end the event stream; safe to repeat."""
        if self._closed:
            return
        self._closed = True
        outcomes: Sequence[BaseException | None] = ()
        try:
            await self._finish()
        finally:
            self._live = False
            try:
                for reader in self._readers:
                    reader.cancel()
                # The reader's cancellation is collected; one of whoever is closing propagates.
                outcomes = await asyncio.gather(*self._readers, return_exceptions=True)
            finally:
                await self._drop_connection()
                self._outbox.abandon()
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                raise outcome

    # ----------------------------------------------------------------------------------- port

    async def send_audio(self, frame: AudioFrame) -> None:
        self._ensure_usable()
        if frame.format != self._setup.input_format:
            raise ProviderError(
                self._setup.provider,
                f"a frame in {frame.format} reached a session opened for "
                f"{self._setup.input_format}",
                retryable=False,
            )
        audio = self._inbound.convert(frame.data)
        if audio:
            await self._send(self._audio_event(audio))

    def events(self) -> AsyncIterator[SpeechEvent]:
        return self._outbox.events()

    # ------------------------------------------------------------------------------ the reader

    async def _read(self) -> None:
        try:
            await self._converse()
        except Exception:
            # The consumer is told the session ended; `close` raises the defect itself.
            self._fail("the session stopped unexpectedly")
            raise

    async def _converse(self) -> None:
        while self._failure is None and (connection := self._connection) is not None:
            received = await receive(connection)
            if isinstance(received, EventConnectionError):
                await self._connection_failed(received)
                continue
            signal = self._parse(received)
            if signal is None:
                continue
            if self._unproven and self._proves(signal):
                self._unproven = False
                self._budget.proven()
            await self._handle(signal)

    def _parse(self, event: Event) -> Signal | None:
        try:
            return self._parse_event(event)
        except MalformedEventError:
            self._telemetry.stream_error(StreamErrorKind.MALFORMED)
            return None

    async def _read_until[T](
        self,
        connection: EventConnection,
        *,
        seconds: float,
        late: str,
        step: Callable[[Event], Awaitable[T | None]],
    ) -> T:
        """Read `connection` until `step` returns something, failing retryably after `seconds`."""
        try:
            async with asyncio.timeout(seconds):
                while True:
                    received = await receive(connection)
                    if isinstance(received, EventConnectionError):
                        raise received
                    result = await step(received)
                    if result is not None:
                        return result
        except TimeoutError:
            raise ConnectionFailedError(late, retryable=True) from None

    # ---------------------------------------------------------------------------- reconnection

    async def _connection_failed(self, error: EventConnectionError) -> None:
        self._telemetry.stream_error(StreamErrorKind.CONNECTION)
        await self._recover(error)

    async def _recover(self, error: EventConnectionError) -> None:
        """Replace a failed connection, or end the session."""
        self._live = False
        await self._drop_connection()
        await self._forget_connection()
        if not is_retryable(error):
            self._fail(str(error))
            return
        replacement = await replace_connection(
            open_connection=self._open,
            abandon=self._drop_connection,
            policy=self._setup.reconnect,
            timekeeping=self._setup.timekeeping,
            telemetry=self._telemetry,
            budget=self._budget,
        )
        if isinstance(replacement, str):
            self._fail(replacement)
            return
        self._unproven = True
        await self._go_live()

    async def _open(self) -> EventConnection:
        # Held before it is introduced, so a failure part-way through still finds it to close.
        connection = self._connection = await self._setup.opener()
        await self._introduce(connection)
        return connection

    async def _go_live(self) -> None:
        self._live = True
        await self._catch_up()

    async def _end(self, reason: str) -> None:
        """End the session over a connection that still works, and let it go."""
        self._fail(reason)
        self._live = False
        await self._drop_connection()

    def _fail(self, reason: str) -> None:
        self._failure = reason
        self._outbox.put(SessionFailed(reason, retryable=False))
        self._outbox.end()

    # ------------------------------------------------------------------------------- plumbing

    async def _send(self, event: Event) -> None:
        """Send when live; a failure is left to the reader, which sees it too and recovers."""
        connection = self._connection
        if not self._live or connection is None:
            return
        try:
            await connection.send(event)
        except EventConnectionError:
            return

    async def _drop_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            await connection.close()

    def _ensure_usable(self) -> None:
        if self._failure is not None:
            raise ProviderError(self._setup.provider, self._failure, retryable=False)
        if self._closed:
            raise ProviderError(self._setup.provider, "the session is closed", retryable=False)
