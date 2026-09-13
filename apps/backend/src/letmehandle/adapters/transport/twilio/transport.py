"""Streaming calls, each a conference of caller, assistant leg and user leg (D-027)."""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from letmehandle.adapters.transport.twilio import twiml
from letmehandle.adapters.transport.twilio.callbacks import (
    CALL_PARAMETER,
    LEG_PARAMETER,
    TOKEN_PARAMETER,
    ConferenceEvent,
    ConferenceUpdate,
    IncomingCall,
    LegProgress,
    LegStatus,
)
from letmehandle.adapters.transport.twilio.media import (
    INBOUND_TRACK,
    MediaProtocolError,
    MediaReceived,
    StreamStarted,
    StreamStopped,
    parse_message,
)
from letmehandle.adapters.transport.twilio.rest import PROVIDER, ParticipantRequest
from letmehandle.adapters.transport.twilio.stream import (
    CallAudioSink,
    CallAudioSource,
    MediaStream,
)
from letmehandle.domain.errors import IllegalTransitionError, InvariantError, ProviderError
from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND, AudioFormat
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallEvent,
    CallEventKind,
    CallTransport,
    ParticipantOutcome,
    ParticipantRole,
    TransportCapabilities,
)
from letmehandle.observability.logging import correlation_id, get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine

    from letmehandle.adapters.transport.twilio.rest import TelephonyApi
    from letmehandle.adapters.transport.twilio.signature import SignatureVerifier
    from letmehandle.adapters.transport.twilio.stream import MediaSocket
    from letmehandle.domain.models.audio import AudioFrame
    from letmehandle.domain.ports.audio_io import AudioSink, AudioSource

logger = get_logger(__name__)

# Paths the provider calls; the provider's console points at the first two.
INCOMING_PATH: Final = "/telephony/voice/incoming"
ASSISTANT_PATH: Final = "/telephony/voice/assistant"
CONFERENCE_PATH: Final = "/telephony/conference/status"
LEG_PATH: Final = "/telephony/leg/status"
CALLER_PATH: Final = "/telephony/voice/caller-left"
MEDIA_PATH: Final = "/telephony/media"

CALLER_LABEL: Final = "caller"
_ASSISTANT_PREFIX: Final = "assistant-"
_USER_PREFIX: Final = "user-"

# How long a dialled leg rings: long enough to find a phone, short of a voicemail pickup.
USER_DIAL_TIMEOUT_SECONDS: Final = 30
ASSISTANT_DIAL_TIMEOUT_SECONDS: Final = 15

# How many finished calls and delivery tokens are remembered against redelivered callbacks.
REMEMBERED_DELIVERIES: Final = 10_000

# How long a leg completed without joining waits for its late conference callbacks.
LATE_CALLBACK_GRACE_SECONDS: Final = 2.0

# How long an accepted media websocket has to start its stream before it is closed.
MEDIA_START_SECONDS: Final = 5.0

# How long shutdown waits for the provider to end the calls still in progress.
SHUTDOWN_SECONDS: Final = 5.0

_OUTCOMES: Final = {
    LegStatus.NO_ANSWER: ParticipantOutcome.NO_ANSWER,
    LegStatus.BUSY: ParticipantOutcome.BUSY,
    LegStatus.FAILED: ParticipantOutcome.FAILED,
    LegStatus.CANCELED: ParticipantOutcome.FAILED,
}


@dataclass(frozen=True, slots=True)
class TwilioConfig:
    """One line's account, its numbers, and the prefix every path its provider calls begins with."""

    account_id: str
    app_id: str
    numbers: tuple[PhoneNumber, ...]
    path_prefix: str = ""

    def __post_init__(self) -> None:
        if not self.numbers:
            raise InvariantError("a streaming transport needs a number to place calls from")
        if self.path_prefix and (
            not self.path_prefix.startswith("/") or self.path_prefix.endswith("/")
        ):
            raise InvariantError("a path prefix starts with a slash and does not end with one")


@dataclass(frozen=True, slots=True)
class Forwarding:
    """Whether the carrier said a call was forwarded, and the line it named if that is a number."""

    forwarded: bool
    line: PhoneNumber | None


@dataclass(frozen=True, slots=True)
class _Applied:
    muted: bool = False
    coach_call_sid: str | None = None


@dataclass(eq=False)
class _AssistantMedia:
    """An assistant leg's stream, and what a websocket must present to carry it."""

    stream: MediaStream
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    token_spent: bool = False
    # The application's own call, which asks for the stream and starts it; never the participant.
    application_sid: str | None = None

    def bind_application(self, call_sid: str) -> bool:
        """Bind the application call asking for the stream; False when another call is bound."""
        if self.application_sid is None:
            self.application_sid = call_sid
        return self.application_sid == call_sid

    def admit(self, call_sid: str, presented: str | None) -> bool:
        """Whether a stream start may attach, spending the token it presents: one start only."""
        if self.token_spent or presented is None:
            return False
        if not hmac.compare_digest(presented.encode(), self.token.encode()):
            return False
        self.token_spent = True
        return call_sid == self.application_sid


@dataclass(eq=False)
class _Leg:
    """One dialled participant: the assistant or a user."""

    label: str
    role: ParticipantRole
    number: PhoneNumber | None = None
    media: _AssistantMedia | None = None
    # The participant's call, named by leg and conference callbacks and by every request about it.
    participant_sid: str | None = None
    answered: bool = False
    joined: bool = False
    finished: bool = False
    removed: bool = False
    # Reported unreachable only because nothing arrived in time, so a late join can correct it.
    given_up_on: bool = False
    last_progress: int = -1
    last_conference: int = -1
    applied: _Applied = field(default_factory=_Applied)


@dataclass(eq=False)
class _Call:
    """Everything one call holds open, and the history needed to read its callbacks."""

    call_id: CallId
    caller: Caller
    from_number: PhoneNumber
    forwarding: Forwarding
    conference_sid: str | None = None
    legs: dict[str, _Leg] = field(default_factory=dict)
    presence: AssistantPresence = AssistantPresence.STAY
    answered: bool = False
    ended: bool = False
    released: bool = False
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    counter: int = 0

    @property
    def conference_name(self) -> str:
        return _conference_name(self.call_id)

    def next_label(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def assistant(self) -> _Leg | None:
        legs = [leg for leg in self.legs.values() if leg.role is ParticipantRole.ASSISTANT]
        return next((leg for leg in reversed(legs) if not leg.finished and not leg.removed), None)

    def present_users(self) -> list[_Leg]:
        return [
            leg
            for leg in self.legs.values()
            if leg.role is ParticipantRole.USER and leg.joined and not leg.finished
        ]

    def current_stream(self) -> MediaStream | None:
        assistant = self.assistant()
        media = assistant.media if assistant is not None else None
        return media.stream if media is not None else None


class _Remembered:
    """A bounded set, oldest forgotten first."""

    def __init__(self, capacity: int) -> None:
        self._items: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def __contains__(self, item: str) -> bool:
        return item in self._items

    def add(self, item: str) -> bool:
        """Remember an item. False when it was already remembered."""
        if item in self._items:
            return False
        self._items[item] = None
        if len(self._items) > self._capacity:
            self._items.popitem(last=False)
        return True


class TwilioCallTransport(CallTransport):
    """The streaming transport: answering, conversation, bridging and three-party calls."""

    def __init__(
        self,
        *,
        config: TwilioConfig,
        api: TelephonyApi,
        verifier: SignatureVerifier,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._api = api
        self._verifier = verifier
        self._monotonic = monotonic
        self._calls: dict[CallId, _Call] = {}
        self._finished = _Remembered(REMEMBERED_DELIVERIES)
        self._deliveries = _Remembered(REMEMBERED_DELIVERIES)
        self._events: asyncio.Queue[CallEvent | None] = asyncio.Queue()
        self._tasks: set[asyncio.Task[None]] = set()
        self._sockets: set[MediaSocket] = set()
        self._closed = False

    # ------------------------------------------------------------------ identity

    @property
    def name(self) -> str:
        return PROVIDER

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_answer_under_program_control=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
            can_bridge_human=True,
            supports_three_way_call=True,
        )

    @property
    def verifier(self) -> SignatureVerifier:
        return self._verifier

    @property
    def account_id(self) -> str:
        return self._config.account_id

    @property
    def path_prefix(self) -> str:
        """What every path this transport's provider calls begins with."""
        return self._config.path_prefix

    # ---------------------------------------------------------- what is held open

    @property
    def active_calls(self) -> int:
        return len(self._calls)

    @property
    def open_media_sockets(self) -> int:
        return len(self._sockets)

    @property
    def pending_tasks(self) -> int:
        return len(self._tasks)

    def forwarding(self, call_id: CallId) -> Forwarding | None:
        """How a call in progress reached the account; None when no such call is in progress."""
        call = self._calls.get(call_id)
        return None if call is None else call.forwarding

    async def settled(self) -> None:
        """Wait until every task this transport started has finished."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            # Lets done callbacks run, which is when finished tasks leave the set.
            await asyncio.sleep(0)

    # -------------------------------------------------------------- the port

    async def events(self) -> AsyncIterator[CallEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def answer(self, call_id: CallId) -> None:
        """Dial the assistant into the conference unless one is already dialling or present."""
        call = self._require_call(call_id)
        async with call.lock:
            if call.assistant() is not None:
                return
            label = call.next_label(_ASSISTANT_PREFIX)
            leg = _Leg(label, ParticipantRole.ASSISTANT)
            # The stream reads the leg's mute so a listening-only assistant sends no audio.
            leg.media = _AssistantMedia(
                MediaStream(monotonic=self._monotonic, is_muted=lambda: leg.applied.muted)
            )
            call.legs[label] = leg
            call.presence = AssistantPresence.STAY
            target = (
                "app:"
                + self._config.app_id
                + "?"
                + urlencode({CALL_PARAMETER: call_id.value, LEG_PARAMETER: label})
            )
            await self._dial(
                call, leg, target, ASSISTANT_DIAL_TIMEOUT_SECONDS, detect_machine=False
            )

    async def terminate(self, call_id: CallId) -> None:
        call = self._calls.get(call_id)
        if call is None:
            if call_id.value not in self._finished:
                await self._end_unheld(call_id)
            return
            # Under the call's lock, so a dial being placed has an identifier to be ended by.
        async with call.lock:
            try:
                await self._end_remotely(call)
            finally:
                # Released even when the provider refused, so the call is never held forever.
                await self._release(call, "the call was ended")

    def audio_format(self) -> AudioFormat:
        return TELEPHONY_NARROWBAND

    def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        return self.audio_source(call_id).frames()

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        await self.audio_sink(call_id).write(frame)

    def audio_source(self, call_id: CallId) -> AudioSource:
        return CallAudioSource(self._require_call(call_id))

    def audio_sink(self, call_id: CallId) -> AudioSink:
        return CallAudioSink(self._require_call(call_id))

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        """Dial the user into the call. How it turns out arrives as events, not as a result."""
        call = self._require_call(call_id)
        async with call.lock:
            if any(
                leg.number == number and not leg.finished and not leg.removed
                for leg in call.legs.values()
            ):
                return
            label = call.next_label(_USER_PREFIX)
            leg = _Leg(label, ParticipantRole.USER, number=number)
            call.legs[label] = leg
            await self._dial(
                call, leg, number.value, USER_DIAL_TIMEOUT_SECONDS, detect_machine=True
            )

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        call = self._calls.get(call_id)
        if call is None:
            return
        async with call.lock:
            for leg in call.legs.values():
                if leg.number == number and not leg.finished and not leg.removed:
                    await self._hang_up_leg(call, leg)

    async def set_assistant_presence(self, call_id: CallId, presence: AssistantPresence) -> None:
        call = self._require_call(call_id)
        async with call.lock:
            if call.presence is AssistantPresence.LEAVE and call.assistant() is None:
                raise IllegalTransitionError(call.presence, presence)
            call.presence = presence
            await self._apply_presence(call)

    async def close(self) -> None:
        """End every call at the provider within `SHUTDOWN_SECONDS`, then release calls, tasks."""
        if self._closed:
            return
        self._closed = True
        calls = list(self._calls.values())
        try:
            async with asyncio.timeout(SHUTDOWN_SECONDS):
                outcomes = await asyncio.gather(
                    *(self.terminate(call.call_id) for call in calls), return_exceptions=True
                )
        except TimeoutError:
            logger.warning("telephony.shutdown.incomplete", calls=len(self._calls))
        else:
            for outcome in outcomes:
                if isinstance(outcome, Exception):
                    logger.warning("telephony.shutdown.refused", error=type(outcome).__name__)
        for call in calls:
            await self._release(call, "the service is shutting down")
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._api.close()
        self._events.put_nowait(None)

    # --------------------------------------------------- provider callbacks

    def is_repeat_delivery(self, token: str | None) -> bool:
        """Whether a delivery carrying this idempotency token has been handled before."""
        return token is not None and not self._deliveries.add(f"token:{token}")

    def incoming_call(self, incoming: IncomingCall) -> str:
        """A call has arrived. Answer it into its conference and say so."""
        call_id = CallId(incoming.call_sid)
        if self._closed or call_id.value in self._finished:
            return twiml.hang_up()
        call = self._calls.get(call_id)
        if call is None:
            call = _Call(
                call_id=call_id,
                caller=Caller(number=_number_or_none(incoming.caller)),
                from_number=self._number_to_call_from(incoming.called),
                forwarding=Forwarding(
                    forwarded=incoming.forwarded_from is not None,
                    line=_number_or_none(incoming.forwarded_from),
                ),
            )
            self._calls[call_id] = call
            self._emit(CallEventKind.INCOMING, call, "incoming", caller=call.caller)
        return twiml.caller_conference(
            conference_name=call.conference_name,
            status_callback_url=self._callback_url(CONFERENCE_PATH, call),
            participant_label=CALLER_LABEL,
            dial_action_url=self._callback_url(CALLER_PATH, call),
        )

    def caller_left(self, call_value: str | None, call_sid: str) -> str:
        """The caller's dial into their conference is over, so their call is: hang up."""
        call = self._calls.get(CallId(call_value)) if call_value else None
        if call is not None and call.call_id.value == call_sid and not call.ended:
            self._ended_by_provider(call, "the caller hung up")
        return twiml.hang_up()

    def assistant_joining(self, params: dict[str, str], call_sid: str) -> str:
        """The assistant's leg asks what to do: stream, if it is the leg this call is expecting."""
        call = (
            self._calls.get(CallId(params[CALL_PARAMETER])) if params.get(CALL_PARAMETER) else None
        )
        leg = call.legs.get(params.get(LEG_PARAMETER, "")) if call is not None else None
        media = leg.media if leg is not None else None
        if (
            call is None
            or leg is None
            or media is None
            or leg.finished
            or media.stream.has_ended
            or not media.bind_application(call_sid)
        ):
            return twiml.hang_up()
        return twiml.assistant_stream(
            stream_url=self._verifier.websocket_url(self._config.path_prefix + MEDIA_PATH),
            parameters={
                CALL_PARAMETER: call.call_id.value,
                LEG_PARAMETER: leg.label,
                TOKEN_PARAMETER: media.token,
            },
        )

    def conference_updated(self, call_value: str | None, update: ConferenceUpdate) -> None:
        call = self._calls.get(CallId(call_value)) if call_value else None
        if call is None or call.ended:
            return
        if call.conference_sid is None:
            call.conference_sid = update.conference_sid
        elif call.conference_sid != update.conference_sid:
            return
        if not self._deliveries.add(f"conference:{update.conference_sid}:{update.sequence}"):
            return
        match update.event:
            case ConferenceEvent.END:
                self._ended_by_provider(call, update.reason or "the conference ended")
            case ConferenceEvent.JOIN | ConferenceEvent.LEAVE:
                self._participant_changed(call, update)
            case _:
                pass

    def leg_progressed(
        self, call_value: str | None, label: str | None, progress: LegProgress
    ) -> None:
        call = self._calls.get(CallId(call_value)) if call_value else None
        leg = call.legs.get(label or "") if call is not None else None
        if call is None or leg is None or call.ended:
            return
        key = f"leg:{progress.call_sid}:{progress.sequence}:{progress.status}"
        if not self._deliveries.add(key) or progress.status is None:
            return
        leg.participant_sid = leg.participant_sid or progress.call_sid
        if progress.sequence is not None:
            if progress.sequence <= leg.last_progress:
                return
            leg.last_progress = progress.sequence
        if leg.finished:
            return
        if progress.status is LegStatus.IN_PROGRESS:
            leg.answered = True
            if progress.answered_by_machine and leg.role is ParticipantRole.USER:
                self._unreachable(call, leg, ParticipantOutcome.ANSWERED_BY_MACHINE)
                self._spawn(call, self._hang_up_leg(call, leg))
            return
        if not progress.status.is_final:
            return
        if leg.joined:
            # A joined leg whose call is over has left, whether or not the leave arrives.
            self._left(call, leg)
            return
        if leg.removed:
            # Taken off the call on request before joining: nothing happened that was not asked.
            leg.finished = True
            self._end_stream(call, leg)
            return
        if progress.answered_by_machine and leg.role is ParticipantRole.USER:
            self._unreachable(call, leg, ParticipantOutcome.ANSWERED_BY_MACHINE)
            return
        if progress.status is LegStatus.COMPLETED:
            self._spawn(call, self._unreachable_unless_heard_of(call, leg))
            return
        self._unreachable(call, leg, _OUTCOMES[progress.status])

    async def media_connected(self, socket: MediaSocket) -> None:
        """Run one assistant leg's media websocket until it stops, closes or fails."""
        self._sockets.add(socket)
        stream: MediaStream | None = None
        try:
            try:
                async with asyncio.timeout(MEDIA_START_SECONDS):
                    stream = await self._attach(socket)
            except TimeoutError:
                logger.warning("telephony.media.never_started")
                return
            if stream is None:
                return
            while (text := await socket.receive()) is not None:
                message = parse_message(text)
                if isinstance(message, StreamStopped):
                    return
                if isinstance(message, MediaReceived) and message.track == INBOUND_TRACK:
                    stream.receive(message.payload)
        except MediaProtocolError as error:
            logger.warning("telephony.media.protocol_error", reason=str(error))
        finally:
            if stream is not None:
                await stream.end()
            await socket.close()
            self._sockets.discard(socket)

    # ----------------------------------------------------------- internals

    async def _attach(self, socket: MediaSocket) -> MediaStream | None:
        while (text := await socket.receive()) is not None:
            message = parse_message(text)
            if not isinstance(message, StreamStarted):
                if isinstance(message, (MediaReceived, StreamStopped)):
                    raise MediaProtocolError("audio arrived before the stream started")
                continue
            call = self._calls.get(CallId(message.parameters.get(CALL_PARAMETER) or "-"))
            leg = call.legs.get(message.parameters.get(LEG_PARAMETER, "")) if call else None
            media = leg.media if leg is not None else None
            if (
                leg is None
                or media is None
                or not media.admit(message.call_sid, message.parameters.get(TOKEN_PARAMETER))
                or leg.finished
                or media.stream.has_ended
                or media.stream.is_connected
            ):
                logger.warning("telephony.media.unexpected_stream")
                return None
            media.stream.attach(socket, message.stream_sid)
            return media.stream
        return None

    async def _dial(
        self, call: _Call, leg: _Leg, target: str, ring_seconds: int, *, detect_machine: bool
    ) -> None:
        request = ParticipantRequest(
            to=target,
            from_=call.from_number.value,
            label=leg.label,
            status_callback_url=self._callback_url(LEG_PATH, call, leg),
            conference_status_callback_url=self._callback_url(CONFERENCE_PATH, call),
            timeout_seconds=ring_seconds,
            detect_machine=detect_machine,
        )
        try:
            call_sid = await self._api.create_participant(call.conference_name, request)
        except (ProviderError, asyncio.CancelledError):
            # Cancelled counts as failed: a leg with no identifier can never be hung up.
            leg.finished = True
            self._end_stream(call, leg)
            raise
        leg.participant_sid = call_sid
        if call.released:
            # Released during the dial by a shutdown: nothing will hear this leg, so it is ended.
            leg.finished = True
            await self._api.end_call(call_sid, "canceled")
            raise ProviderError(
                PROVIDER, "the call ended while it was being dialled", retryable=False
            )

    def _participant_changed(self, call: _Call, update: ConferenceUpdate) -> None:
        if update.label == CALLER_LABEL:
            if update.event is ConferenceEvent.LEAVE:
                self._ended_by_provider(call, "the caller hung up")
            elif not call.answered:
                call.answered = True
                self._emit(CallEventKind.ANSWERED, call, "answered")
            return
        leg = call.legs.get(update.label or "")
        if leg is None and update.call_sid is not None:
            leg = next(
                (each for each in call.legs.values() if each.participant_sid == update.call_sid),
                None,
            )
        if leg is not None and leg.given_up_on:
            # A leg given up on was on the call after all, and its call is over: joined and left.
            leg.given_up_on = False
            self._joined(call, leg)
            self._left(call, leg)
            return
        if leg is None or update.sequence <= leg.last_conference or leg.finished:
            return
        leg.last_conference = update.sequence
        leg.participant_sid = leg.participant_sid or update.call_sid
        if update.event is ConferenceEvent.JOIN:
            if leg.joined:
                return
            self._joined(call, leg)
            self._spawn(call, self._apply_presence_locked(call))
            return
        self._left(call, leg)

    def _joined(self, call: _Call, leg: _Leg) -> None:
        leg.joined = True
        outcome = ParticipantOutcome.ANSWERED if leg.role is ParticipantRole.USER else None
        self._emit(
            CallEventKind.PARTICIPANT_JOINED,
            call,
            f"joined:{leg.label}",
            participant=leg.role,
            outcome=outcome,
        )

    def _left(self, call: _Call, leg: _Leg) -> None:
        leg.finished = True
        self._emit(CallEventKind.PARTICIPANT_LEFT, call, f"left:{leg.label}", participant=leg.role)
        self._end_stream(call, leg)
        if leg.role is ParticipantRole.USER:
            self._spawn(call, self._apply_presence_locked(call))

    def _unreachable(self, call: _Call, leg: _Leg, outcome: ParticipantOutcome) -> None:
        leg.finished = True
        self._emit(
            CallEventKind.PARTICIPANT_UNREACHABLE,
            call,
            f"unreachable:{leg.label}",
            participant=leg.role,
            outcome=outcome,
        )
        self._end_stream(call, leg)

    async def _unreachable_unless_heard_of(self, call: _Call, leg: _Leg) -> None:
        await asyncio.sleep(LATE_CALLBACK_GRACE_SECONDS)
        if leg.joined or leg.finished:
            return
        if leg.answered and leg.role is ParticipantRole.USER:
            # A user who answered was put in the conference; only its callbacks were lost.
            self._joined(call, leg)
            self._left(call, leg)
            return
        self._unreachable(call, leg, ParticipantOutcome.FAILED)
        leg.given_up_on = True

    async def _apply_presence_locked(self, call: _Call) -> None:
        async with call.lock:
            await self._apply_presence(call)

    async def _apply_presence(self, call: _Call) -> None:
        """Make the assistant's participant match the policy for who is on the call now."""
        assistant = call.assistant()
        if assistant is None or call.ended:
            return
        if call.presence is AssistantPresence.LEAVE:
            await self._hang_up_leg(call, assistant)
            return
        if not assistant.joined or call.conference_sid is None or assistant.participant_sid is None:
            return
        users = call.present_users()
        if not users or call.presence is AssistantPresence.STAY:
            target = _Applied()
        elif call.presence is AssistantPresence.LISTEN_ONLY:
            target = _Applied(muted=True)
        else:
            target = _Applied(coach_call_sid=users[-1].participant_sid)
        if target == assistant.applied:
            return
        await self._api.update_participant(
            call.conference_sid,
            assistant.participant_sid,
            muted=target.muted,
            coach_call_sid=target.coach_call_sid,
        )
        assistant.applied = target

    async def _hang_up_leg(self, call: _Call, leg: _Leg) -> None:
        leg.removed = True
        # Every caller holds the call's lock or has bound the identifier; narrowed for the type.
        if leg.participant_sid is None:  # pragma: no cover - unreachable while dials hold the lock
            return
        if leg.joined and call.conference_sid is not None:
            await self._api.remove_participant(call.conference_sid, leg.participant_sid)
        else:
            await self._api.end_call(
                leg.participant_sid, "completed" if leg.answered else "canceled"
            )

    async def _end_remotely(self, call: _Call) -> None:
        """End the call at the provider, trying every step and raising the first failure after."""
        failures: list[ProviderError] = []

        async def attempt(step: Awaitable[object]) -> None:
            try:
                await step
            except ProviderError as failure:
                failures.append(failure)

        conference_sid = call.conference_sid
        if conference_sid is not None:
            await attempt(self._api.end_conference(conference_sid))
            # The caller's leg and ringing legs are ended apart: neither need be in the conference.
        await attempt(self._api.end_call(call.call_id.value, "completed"))
        for leg in list(call.legs.values()):
            if not leg.joined and not leg.finished and not leg.removed:
                await attempt(self._hang_up_leg(call, leg))
        if failures:
            raise failures[0]

    async def _end_unheld(self, call_id: CallId) -> None:
        """End a call a stopped process left: the caller's leg, its conference and dialled user."""
        failures: list[ProviderError] = []
        steps: tuple[Callable[[], Awaitable[object]], ...] = (
            lambda: self._api.end_call(call_id.value, "completed"),
            lambda: self._api.end_conferences_named(_conference_name(call_id)),
            lambda: self._end_dialled_for(call_id),
        )
        for step in steps:
            try:
                await step()
            except ProviderError as failure:
                failures.append(failure)
        if failures:
            raise failures[0]

    async def _end_dialled_for(self, call_id: CallId) -> None:
        """End legs from the number the call reached to the line that forwarded it (D-033)."""
        found = await self._api.find_call(call_id.value)
        line = None if found is None else _number_or_none(found.forwarded_from)
        if found is None or line is None:
            return
        await self._api.end_calls_between(self._number_to_call_from(found.to).value, line.value)

    def _ended_by_provider(self, call: _Call, reason: str) -> None:
        self._emit(CallEventKind.ENDED, call, "ended", detail=reason)
        self._spawn_detached(self._finish_after_provider(call, reason))

    async def _finish_after_provider(self, call: _Call, reason: str) -> None:
        async with call.lock:
            try:
                for leg in list(call.legs.values()):
                    # A leg still ringing would join an empty conference if answered.
                    if not leg.joined and not leg.finished and not leg.removed:
                        await self._hang_up_leg(call, leg)
            finally:
                await self._release(call, reason)

    async def _release(self, call: _Call, reason: str) -> None:
        """Let go of everything the call holds on this side. Safe to call more than once."""
        if call.released:
            return
        call.released = True
        self._emit(CallEventKind.ENDED, call, "ended", detail=reason)
        self._calls.pop(call.call_id, None)
        self._finished.add(call.call_id.value)
        for task in list(call.tasks):
            task.cancel()
        for leg in call.legs.values():
            if leg.media is not None:
                await leg.media.stream.end()

    def _end_stream(self, call: _Call, leg: _Leg) -> None:
        if leg.media is not None and not leg.media.stream.has_ended:
            self._spawn(call, leg.media.stream.end())

    def _emit(
        self,
        kind: CallEventKind,
        call: _Call,
        discriminator: str,
        *,
        caller: Caller | None = None,
        detail: str | None = None,
        participant: ParticipantRole | None = None,
        outcome: ParticipantOutcome | None = None,
    ) -> None:
        if kind is CallEventKind.ENDED:
            if call.ended:
                return
            call.ended = True
        self._events.put_nowait(
            CallEvent(
                kind,
                call.call_id,
                EventId(f"{call.call_id}:{discriminator}"),
                caller=caller,
                detail=detail,
                participant=participant,
                outcome=outcome,
                correlation_id=correlation_id.get(),
            )
        )

    def _spawn(self, call: _Call, work: Coroutine[object, object, None]) -> None:
        task = self._spawn_detached(work, call)
        call.tasks.add(task)
        task.add_done_callback(call.tasks.discard)

    def _spawn_detached(
        self, work: Coroutine[object, object, None], call: _Call | None = None
    ) -> asyncio.Task[None]:
        task = asyncio.get_running_loop().create_task(work)
        self._tasks.add(task)
        task.add_done_callback(lambda done: self._task_done(done, call))
        return task

    def _task_done(self, task: asyncio.Task[None], call: _Call | None) -> None:
        self._tasks.discard(task)
        if task.cancelled() or task.exception() is None:
            return
        failure = task.exception()
        # Background work failed with nobody awaiting it, so the orchestrator hears of it.
        logger.warning("telephony.background_failed", error=type(failure).__name__)
        if call is not None and not call.released:
            self._events.put_nowait(
                CallEvent(
                    CallEventKind.FAILED,
                    call.call_id,
                    EventId(f"{call.call_id}:failed:{id(task)}"),
                    detail=str(failure),
                    correlation_id=correlation_id.get(),
                )
            )

    def _require_call(self, call_id: CallId) -> _Call:
        call = self._calls.get(call_id)
        if call is None:
            raise ProviderError(PROVIDER, "no such call is in progress", retryable=False)
        return call

    def _number_to_call_from(self, called: str | None) -> PhoneNumber:
        dialled = _number_or_none(called)
        return dialled if dialled in self._config.numbers else self._config.numbers[0]

    def _callback_url(self, path: str, call: _Call, leg: _Leg | None = None) -> str:
        query = {CALL_PARAMETER: call.call_id.value}
        if leg is not None:
            query[LEG_PARAMETER] = leg.label
        return self._verifier.url_for(self._config.path_prefix + path, urlencode(query))


def _number_or_none(raw: str | None) -> PhoneNumber | None:
    """A number the provider supplied, or nothing: withheld and malformed are the same to a rule."""
    if raw is None:
        return None
    try:
        return PhoneNumber.parse(raw)
    except InvariantError:
        return None


def _conference_name(call_id: CallId) -> str:
    return f"call-{call_id}"
