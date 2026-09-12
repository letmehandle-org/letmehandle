"""In-memory implementations, used to exercise the contracts in phase 1.

These are not mocks. They implement the behaviour the contract describes — a queue that really
is bounded, an interruption that really discards what was queued — so that a test passing
against them means the contract is satisfiable, and a later real adapter passing the same suite
means the same thing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING, TypeVar

from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    CallTransport,
    ScreeningDecision,
    TransportCapabilities,
)
from letmehandle.domain.ports.clock import Clock, IdGenerator
from letmehandle.domain.ports.llm import LLMCapabilities, LLMProvider, Message
from letmehandle.domain.ports.notification import (
    DeliveryOutcome,
    DeliveryStatus,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
    NotificationProvider,
)
from letmehandle.domain.ports.otp import OTPProvider
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SpeechCapabilities,
    SpeechEvent,
    SpeechProvider,
    SpeechSession,
    SpeechStarted,
)
from letmehandle.domain.ports.voice import (
    Voice,
    VoiceCapabilities,
    VoiceProvider,
    VoiceSample,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from letmehandle.domain.models.phone_number import PhoneNumber

T = TypeVar("T")


class FixedClock(Clock):
    """A clock that does not move unless told to."""

    def __init__(self, at_instant: datetime | None = None) -> None:
        self._now = at_instant or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = datetime.fromtimestamp(self._now.timestamp() + seconds, tz=UTC)


class CountingIdGenerator(IdGenerator):
    """Predictable identifiers, so a test can name what it expects."""

    def __init__(self, prefix: str = "id") -> None:
        self._prefix = prefix
        self._numbers = count(1)

    def generate(self) -> str:
        return f"{self._prefix}-{next(self._numbers)}"


class RecordingOTPProvider(OTPProvider):
    """Delivers nowhere and remembers everything, which is what development needs."""

    def __init__(self) -> None:
        self.sent: list[tuple[PhoneNumber, str]] = []

    @property
    def name(self) -> str:
        return "recording"

    @property
    def is_safe_for_production(self) -> bool:
        return False

    async def send(self, number: PhoneNumber, code: str) -> None:
        self.sent.append((number, code))


class RecordingNotificationProvider(NotificationProvider):
    """Reports whatever it is told to report, so failure paths can be exercised."""

    def __init__(
        self,
        platform: DevicePlatform = DevicePlatform.IOS,
        status: DeliveryStatus = DeliveryStatus.DELIVERED,
    ) -> None:
        self._platform = platform
        self.status = status
        self.sent: list[tuple[DeviceToken, EscalationNotification]] = []

    @property
    def name(self) -> str:
        return "recording"

    @property
    def platform(self) -> DevicePlatform:
        return self._platform

    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        if token.platform is not self._platform:
            raise ProviderError(
                self.name,
                f"a {token.platform} token cannot be sent by {self._platform}",
                retryable=False,
            )
        self.sent.append((token, notification))
        return DeliveryOutcome(self.status)


class ScriptedLLMProvider(LLMProvider):
    """Answers from a script, so decisions under test are decisions, not guesses."""

    def __init__(self, replies: Sequence[str] | None = None) -> None:
        self._replies = list(replies or ["an answer"])
        self.seen: list[Sequence[Message]] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return "scripted-1"

    @property
    def capabilities(self) -> LLMCapabilities:
        return LLMCapabilities(structured_output=True, tool_calling=True, max_context_tokens=8192)

    async def complete(self, messages: Sequence[Message]) -> str:
        self.seen.append(messages)
        return self._replies[min(len(self.seen), len(self._replies)) - 1]

    async def complete_structured(self, messages: Sequence[Message], schema: type[T]) -> T:
        self.seen.append(messages)
        # A scripted provider cannot know what the caller's schema means, so it returns an
        # empty instance. Tests that care about the contents supply their own provider.
        return schema()


class StaticVoiceProvider(VoiceProvider):
    """A fixed catalogue, with control over what is currently available."""

    def __init__(self, *, unavailable: set[str] | None = None, cloning: bool = False) -> None:
        self._unavailable = unavailable or set()
        self._cloning = cloning
        self._voices = (
            Voice(id="calm", name="Calm", locales=("en",)),
            Voice(id="bright", name="Bright", locales=("en", "fr")),
        )

    @property
    def name(self) -> str:
        return "static"

    @property
    def capabilities(self) -> VoiceCapabilities:
        return VoiceCapabilities(
            builtin_voices=True,
            preview=True,
            cloning=self._cloning,
            custom_voice=self._cloning,
        )

    @property
    def default_voice_id(self) -> str:
        return "calm"

    async def list_voices(self, locale: str | None = None) -> Sequence[Voice]:
        if locale is None:
            return self._voices
        return tuple(voice for voice in self._voices if voice.speaks(locale))

    async def is_available(self, voice_id: str) -> bool:
        known = {voice.id for voice in self._voices} | {"cloned"}
        return voice_id in known and voice_id not in self._unavailable

    async def preview(self, voice_id: str) -> VoiceSample:
        if not await self.is_available(voice_id):
            raise ProviderError(self.name, f"no voice named {voice_id!r}", retryable=False)
        return VoiceSample(audio=b"a sample of " + voice_id.encode(), media_type="audio/mpeg")


@dataclass
class _Queued:
    events: asyncio.Queue[SpeechEvent | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=8)
    )


class EchoSpeechSession(SpeechSession):
    """Turns every frame it is given into one frame of output.

    Bounded, interruptible, and closeable more than once, because those are the three things
    the contract insists on and a fake that skipped them would let a broken adapter pass.
    """

    def __init__(self) -> None:
        self._queue = _Queued().events
        self._closed = False
        self.context_updates: list[str] = []
        self.interruptions = 0

    async def send_audio(self, frame: AudioFrame) -> None:
        if self._closed:
            raise ProviderError("echo", "the session is closed", retryable=False)
        await self._queue.put(SpeechStarted(by_caller=False))
        await self._queue.put(AudioProduced(frame))

    async def events(self) -> AsyncIterator[SpeechEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def update_context(self, context: str) -> None:
        self.context_updates.append(context)

    async def interrupt(self) -> None:
        self.interruptions += 1
        # Discarding what was queued is the part real implementations forget, and the reason
        # interruption feels broken when they do.
        while not self._queue.empty():
            self._queue.get_nowait()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def queued(self) -> int:
        return self._queue.qsize()


class EchoSpeechProvider(SpeechProvider):
    """Opens echo sessions, and remembers how it was asked to."""

    def __init__(self) -> None:
        self.sessions: list[EchoSpeechSession] = []
        self.connections: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "echo"

    @property
    def capabilities(self) -> SpeechCapabilities:
        return SpeechCapabilities(
            barge_in=True,
            context_updates_mid_session=True,
            reconnection=True,
            languages=("en", "en-GB"),
            input_formats=(SPEECH_WIDEBAND,),
            output_format=SPEECH_WIDEBAND,
        )

    async def connect(
        self,
        *,
        system_context: str,
        voice_id: str,
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        self.connections.append(
            {
                "system_context": system_context,
                "voice_id": voice_id,
                "locale": locale,
                "input_format": input_format,
            }
        )
        session = EchoSpeechSession()
        self.sessions.append(session)
        return session


class ScreeningOnlyTransport(CallTransport):
    """A transport shaped like a platform's own call screening.

    It sees a call before the handset rings and can allow, reject or silence it. It cannot hand
    the application the call's audio, and it cannot add anybody: declaring otherwise is exactly
    the lie the capability model exists to prevent.
    """

    def __init__(self) -> None:
        self.decisions: list[tuple[CallId, ScreeningDecision]] = []
        self.answered: list[CallId] = []
        self.terminated: list[CallId] = []
        self.pending: list[CallEvent] = []

    @property
    def name(self) -> str:
        return "screening-only"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(can_screen_before_ringing=True, supports_native_ringing=True)

    async def events(self) -> AsyncIterator[CallEvent]:
        for event in self.pending:
            yield event

    async def answer(self, call_id: CallId) -> None:
        self.answered.append(call_id)

    async def terminate(self, call_id: CallId) -> None:
        self.terminated.append(call_id)

    async def screen(self, call_id: CallId, decision: ScreeningDecision) -> None:
        self.decisions.append((call_id, decision))


class StreamingTransport(CallTransport):
    """A transport shaped like programmable telephony.

    It carries audio both ways and can add a third party to a call already in progress. It
    never sees a call before it connects, so it declares no screening.
    """

    def __init__(self) -> None:
        self.answered: list[CallId] = []
        self.terminated: list[CallId] = []
        self.injected: list[AudioFrame] = []
        self.participants: list[PhoneNumber] = []
        self.removed: list[PhoneNumber] = []
        self.incoming: list[AudioFrame] = []
        self.pending: list[CallEvent] = []

    @property
    def name(self) -> str:
        return "streaming"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
            can_bridge_human=True,
            supports_three_way_call=True,
        )

    async def events(self) -> AsyncIterator[CallEvent]:
        for event in self.pending:
            yield event

    async def answer(self, call_id: CallId) -> None:
        self.answered.append(call_id)

    async def terminate(self, call_id: CallId) -> None:
        self.terminated.append(call_id)

    async def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        for frame in self.incoming:
            yield frame

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        self.injected.append(frame)

    def audio_format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        self.participants.append(number)

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        self.removed.append(number)


class LyingTransport(CallTransport):
    """Declares a capability it has not implemented.

    Exists so that the narrowing functions can be shown to catch the one failure the type
    system cannot: a declaration and an implementation that disagree. Without this, that check
    would be untested code claiming to be a safety net.
    """

    @property
    def name(self) -> str:
        return "lying"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_bridge_human=True,
            can_screen_before_ringing=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
        )

    async def events(self) -> AsyncIterator[CallEvent]:
        yield CallEvent(CallEventKind.INCOMING, CallId("c"), EventId("e"))

    async def answer(self, call_id: CallId) -> None:
        return None

    async def terminate(self, call_id: CallId) -> None:
        return None
