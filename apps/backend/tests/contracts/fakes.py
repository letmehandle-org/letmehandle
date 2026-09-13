"""In-memory implementations, used to exercise the contracts in phase 1.

These are not mocks. They implement the behaviour the contract describes — a queue that really
is bounded, an interruption that really discards what was queued — so that a test passing
against them means the contract is satisfiable, and a later real adapter passing the same suite
means the same thing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING

from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.ports.audio_io import AudioSink, AudioSource
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallEvent,
    CallEventKind,
    CallTransport,
    ScreeningDecision,
    TransportCapabilities,
)
from letmehandle.domain.ports.clock import Clock, IdGenerator
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


class FixedClock(Clock):
    """A clock that does not move unless told to."""

    def __init__(self, at_instant: datetime | None = None) -> None:
        self._now = at_instant or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


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


class CheckingOTPProvider(RecordingOTPProvider):
    """Makes and checks its own codes, as a provider that composes its own message does (D-042).

    Its code is the one given, so a test can type it, and each is accepted once. `failure` is raised
    by the next send or check instead, so a provider's outage can be exercised on either.
    """

    def __init__(self, code: str = "987654") -> None:
        super().__init__()
        self.code = code
        self.issued: list[PhoneNumber] = []
        self.checked: list[tuple[PhoneNumber, str]] = []
        self.failure: ProviderError | None = None
        self._pending: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "checking"

    def issues_its_own_codes(self, number: PhoneNumber) -> bool:
        return True

    async def send_own_code(self, number: PhoneNumber) -> None:
        self._fail_if_told()
        self.issued.append(number)
        self._pending[number.value] = self.code

    async def check(self, number: PhoneNumber, code: str) -> bool:
        self.checked.append((number, code))
        self._fail_if_told()
        if self._pending.get(number.value) != code:
            return False
        del self._pending[number.value]
        return True

    def _fail_if_told(self) -> None:
        failure, self.failure = self.failure, None
        if failure is not None:
            raise failure


class RecordingNotificationProvider(NotificationProvider):
    """Reports whatever it is told to report, so failure paths can be exercised."""

    def __init__(
        self,
        platform: DevicePlatform = DevicePlatform.IOS,
        status: DeliveryStatus = DeliveryStatus.DELIVERED,
        limit: int = 4096,
    ) -> None:
        self._platform = platform
        self.status = status
        self.limit = limit
        self.sent: list[tuple[DeviceToken, EscalationNotification]] = []

    @property
    def name(self) -> str:
        return "recording"

    @property
    def platform(self) -> DevicePlatform:
        return self._platform

    @property
    def payload_limit_bytes(self) -> int:
        return self.limit

    def payload_size(self, notification: EscalationNotification) -> int:
        fields = (notification.title, notification.body, notification.caller_label)
        return sum(len(text.encode()) for text in fields) + len(str(notification.data).encode())

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


class StaticVoiceProvider(VoiceProvider):
    """A fixed catalogue, with control over what is currently available."""

    def __init__(self, *, unavailable: set[str] | None = None, cloning: bool = False) -> None:
        self._unavailable = unavailable or set()
        self._cloning = cloning
        self._voices = (
            Voice(id="calm", name="Calm", locales=("en",)),
            Voice(id="bright", name="Bright", locales=("en", "fr")),
            Voice(id="gentle", name="Gentle", locales=("hi",)),
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


class EchoSpeechSession(SpeechSession):
    """Turns every frame it is given into one frame of output.

    Bounded, interruptible, and closeable more than once, because those are the three things
    the contract insists on and a fake that skipped them would let a broken adapter pass.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SpeechEvent | None] = asyncio.Queue(maxsize=8)
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

    async def emit(self, event: SpeechEvent) -> None:
        """Say something as the service would, so a consumer's handling of it can be exercised.

        Echoing only ever reports the model speaking. A caller interrupting, a settled
        transcript and a failure are what a consumer has to get right, and they need a way in.
        """
        await self._queue.put(event)

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
        if self._queue.full():
            # Closing must not wait on a consumer, which may be the thing that stopped. What is
            # queued is dropped only when there is no room left to say the stream has ended.
            self._queue.get_nowait()
        self._queue.put_nowait(None)

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def queued(self) -> int:
        return self._queue.qsize()


class EchoSpeechProvider(SpeechProvider):
    """Opens echo sessions, and remembers how it was asked to."""

    def __init__(self, languages: tuple[str, ...] = ("en", "en-GB")) -> None:
        self.sessions: list[EchoSpeechSession] = []
        self.connections: list[dict[str, object]] = []
        self.languages = languages

    @property
    def name(self) -> str:
        return "echo"

    @property
    def capabilities(self) -> SpeechCapabilities:
        return SpeechCapabilities(
            barge_in=True,
            context_updates_mid_session=True,
            reconnection=True,
            languages=self.languages,
            input_formats=(SPEECH_WIDEBAND,),
            output_format=SPEECH_WIDEBAND,
        )

    async def connect(
        self,
        *,
        system_context: str,
        voice_id: str,
        greeting: str,
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        self.connections.append(
            {
                "system_context": system_context,
                "voice_id": voice_id,
                "greeting": greeting,
                "locale": locale,
                "input_format": input_format,
            }
        )
        session = EchoSpeechSession()
        self.sessions.append(session)
        return session


class ScreeningOnlyTransport(CallTransport):
    """A transport shaped like a platform's own call screening.

    It decides a call before the handset rings — allow, reject or silence — and reports the
    decision on the incoming event. It cannot answer, cannot hand the application the call's
    audio, and cannot add anybody: declaring otherwise is exactly the lie the capability model
    exists to prevent.
    """

    def __init__(self) -> None:
        self.terminated: list[CallId] = []
        self.pending: list[CallEvent] = [
            CallEvent(
                CallEventKind.INCOMING,
                CallId("screened"),
                EventId("screened-incoming"),
                screening=ScreeningDecision.SILENCE,
            )
        ]

    @property
    def name(self) -> str:
        return "screening-only"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(can_screen_before_ringing=True, supports_native_ringing=True)

    async def events(self) -> AsyncIterator[CallEvent]:
        for event in self.pending:
            yield event

    async def terminate(self, call_id: CallId) -> None:
        self.terminated.append(call_id)

    def screening_decisions(self) -> frozenset[ScreeningDecision]:
        return frozenset(ScreeningDecision)

    def screening_deadline(self) -> timedelta:
        return timedelta(seconds=5)


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
        self.presences: list[AssistantPresence] = []

    @property
    def name(self) -> str:
        return "streaming"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_answer_under_program_control=True,
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

    def audio_source(self, call_id: CallId) -> AudioSource:
        return _ListSource(self.incoming, self.audio_format())

    def audio_sink(self, call_id: CallId) -> AudioSink:
        return _ListSink(self.injected, self.audio_format())

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        self.participants.append(number)

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        self.removed.append(number)

    async def set_assistant_presence(self, call_id: CallId, presence: AssistantPresence) -> None:
        self.presences.append(presence)


class _ListSource(AudioSource):
    """A call's audio that is already all there."""

    def __init__(self, frames: list[AudioFrame], audio_format: AudioFormat) -> None:
        self._frames = frames
        self._format = audio_format

    @property
    def format(self) -> AudioFormat:
        return self._format

    async def frames(self) -> AsyncIterator[AudioFrame]:
        for frame in self._frames:
            yield frame


class _ListSink(AudioSink):
    """Plays into a list, and forgets what it has not played when told to."""

    def __init__(self, played: list[AudioFrame], audio_format: AudioFormat) -> None:
        self._played = played
        self._format = audio_format
        self.discards = 0

    @property
    def format(self) -> AudioFormat:
        return self._format

    async def write(self, frame: AudioFrame) -> None:
        self._played.append(frame)

    async def discard(self) -> None:
        self.discards += 1


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
            can_answer_under_program_control=True,
            can_bridge_human=True,
            supports_three_way_call=True,
            can_screen_before_ringing=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
        )

    async def events(self) -> AsyncIterator[CallEvent]:
        yield CallEvent(CallEventKind.INCOMING, CallId("c"), EventId("e"))

    async def terminate(self, call_id: CallId) -> None:
        return None
