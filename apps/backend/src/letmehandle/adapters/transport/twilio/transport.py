"""Streaming calls over programmable telephony, each a conference from the moment it is answered.

The call shape is D-027's. The caller is answered into a conference of their own and their leg is
never touched again. The assistant joins as a second participant, a leg dialled to an
application whose only instruction is to stream its audio to this service. The user, when the
policy calls for them, is dialled into the same conference. Everything the assistant can do
once the user is there — stay, fall silent while listening, speak only to the user, leave — is
one change to one participant.

What the provider tells this transport arrives as HTTP callbacks and websocket messages, and
the provider duplicates, reorders and drops them. So nothing here trusts arrival order: every
callback is recognised as a repeat by the identifiers the provider gave it, and resolved against
the state it describes by the provider's own sequence numbers. Callback handlers change state
and queue events; they never wait on the network, because the provider waits on them. Work that
needs the network — muting the assistant when the user joins, hanging up a voicemail — is
started as a task this transport owns, and every such task is gone when the call is.
"""

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

# Paths the provider calls. The adapter's router serves them; configuration in the provider's
# console points at the first two.
INCOMING_PATH: Final = "/telephony/voice/incoming"
ASSISTANT_PATH: Final = "/telephony/voice/assistant"
CONFERENCE_PATH: Final = "/telephony/conference/status"
LEG_PATH: Final = "/telephony/leg/status"
CALLER_PATH: Final = "/telephony/voice/caller-left"
MEDIA_PATH: Final = "/telephony/media"

CALLER_LABEL: Final = "caller"
_ASSISTANT_PREFIX: Final = "assistant-"
_USER_PREFIX: Final = "user-"

# Long enough for a person to find their phone; short enough that a caller is not left waiting
# on a voicemail the detection would have caught.
USER_DIAL_TIMEOUT_SECONDS: Final = 30
ASSISTANT_DIAL_TIMEOUT_SECONDS: Final = 15

# How many finished calls and delivery tokens are remembered, so a redelivered callback for a
# call that is over cannot start it again. Bounded: a process runs for weeks.
REMEMBERED_DELIVERIES: Final = 10_000

# A leg reported completed without ever joining may simply have had its conference callbacks
# delayed: they are separate requests, and a join and a leave can arrive after the completion.
# The leg is reported unreachable only if nothing about it arrives for this long.
LATE_CALLBACK_GRACE_SECONDS: Final = 2.0

# How long an accepted media websocket has to name the stream it carries. A socket that never
# does is refused, rather than held open for as long as whoever opened it likes.
MEDIA_START_SECONDS: Final = 5.0

# How long shutdown waits for the provider to end the calls still in progress. Bounded, because a
# deployment waiting on an API that does not answer is not shutting down; long enough for a
# handful of requests per call when the API is well.
SHUTDOWN_SECONDS: Final = 5.0

_OUTCOMES: Final = {
    LegStatus.NO_ANSWER: ParticipantOutcome.NO_ANSWER,
    LegStatus.BUSY: ParticipantOutcome.BUSY,
    LegStatus.FAILED: ParticipantOutcome.FAILED,
    LegStatus.CANCELED: ParticipantOutcome.FAILED,
}


@dataclass(frozen=True, slots=True)
class TwilioConfig:
    """The account this transport is, and where the provider reaches it."""

    account_id: str
    app_id: str
    numbers: tuple[PhoneNumber, ...]

    def __post_init__(self) -> None:
        if not self.numbers:
            raise InvariantError("a streaming transport needs a number to place calls from")


@dataclass(frozen=True, slots=True)
class _Applied:
    muted: bool = False
    coach_call_sid: str | None = None


@dataclass(eq=False)
class _Leg:
    """One dialled participant: the assistant or a user."""

    label: str
    role: ParticipantRole
    number: PhoneNumber | None = None
    stream: MediaStream | None = None
    call_sid: str | None = None
    answered: bool = False
    joined: bool = False
    finished: bool = False
    removed: bool = False
    # Reported unreachable only because nothing arrived in time, so a late join can correct it.
    given_up_on: bool = False
    last_progress: int = -1
    last_conference: int = -1
    applied: _Applied = field(default_factory=_Applied)
    # What this leg's stream must present to be attached, and whether anything has presented it.
    # The handshake's signature is the same for every call and a leg's identifier is no secret,
    # so without this any signed socket could name any leg.
    media_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    media_token_spent: bool = False


@dataclass(eq=False)
class _Call:
    """Everything one call holds open, and the history needed to read its callbacks."""

    call_id: CallId
    caller: Caller
    from_number: PhoneNumber
    forwarded_from: PhoneNumber | None = None
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
        return assistant.stream if assistant is not None else None


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

    def forwarded_from(self, call_id: CallId) -> PhoneNumber | None:
        """The line a call in progress was forwarded from, if the carrier said and it is one."""
        call = self._calls.get(call_id)
        return None if call is None else call.forwarded_from

    async def settled(self) -> None:
        """Wait until every task this transport started has finished."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            # Gathering tasks already finished does not yield, and a finished task leaves the
            # set only when its done callback runs on a later turn of the loop.
            await asyncio.sleep(0)

    # -------------------------------------------------------------- the port

    async def events(self) -> AsyncIterator[CallEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def answer(self, call_id: CallId) -> None:
        """Bring the assistant into the call's conference.

        The caller was answered into the conference when the call arrived. Answering in the
        port's sense is the assistant picking up, so that is what this does. Once is enough:
        while an assistant is dialling or present, answering again changes nothing. After the
        assistant has left, it brings a new one.
        """
        call = self._require_call(call_id)
        async with call.lock:
            if call.assistant() is not None:
                return
            label = call.next_label(_ASSISTANT_PREFIX)
            leg = _Leg(label, ParticipantRole.ASSISTANT)
            # Listening only is the conference muting this leg; the stream asks the leg so that
            # it stops sending audio nobody will hear, without being told separately.
            leg.stream = MediaStream(monotonic=self._monotonic, is_muted=lambda: leg.applied.muted)
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
        # Under the call's lock, so a dial still being placed finishes first and its leg has an
        # identifier to be ended by. Otherwise the leg is released unnamed and rings on.
        async with call.lock:
            try:
                await self._end_remotely(call)
            finally:
                # Released whether or not the provider did as asked. A call held here after a
                # refusal could never be ended by trying again: nothing new would be asked.
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
        """End every call in progress, then release every call and every task.

        A caller must not be left alone in a conference because the service went away, so each
        call is ended on the provider's side first — for as long as `SHUTDOWN_SECONDS` allows.
        What could not be ended in that time is released here regardless, and left to the
        conference's own end when the caller hangs up.
        """
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
                forwarded_from=_number_or_none(incoming.forwarded_from),
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
        """The caller's dial into their conference is over, so their call is.

        The provider asks this of the caller's own leg whether they hung up or the conference
        ended around them. The answer is to hang up: there is nothing left to put them in.
        """
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
        if call is None or leg is None:
            call, leg = self._leg_by_call_sid(call_sid)
        if (
            call is None
            or leg is None
            or leg.role is not ParticipantRole.ASSISTANT
            or leg.finished
            or leg.stream is None
            or leg.stream.has_ended
        ):
            return twiml.hang_up()
        leg.call_sid = leg.call_sid or call_sid
        return twiml.assistant_stream(
            stream_url=self._verifier.websocket_url(MEDIA_PATH),
            parameters={
                CALL_PARAMETER: call.call_id.value,
                LEG_PARAMETER: leg.label,
                TOKEN_PARAMETER: leg.media_token,
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
        leg.call_sid = leg.call_sid or progress.call_sid
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
            # The leg's own call is over, so it has left the conference, whether or not the
            # conference's leave for it ever arrives.
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
            stream = leg.stream if leg is not None else None
            if (
                leg is None
                or not self._spend_token(leg, message.parameters.get(TOKEN_PARAMETER))
                or stream is None
                or leg.finished
                or stream.has_ended
                or stream.is_connected
                or (leg.call_sid is not None and leg.call_sid != message.call_sid)
            ):
                logger.warning("telephony.media.unexpected_stream")
                return None
            leg.call_sid = message.call_sid
            stream.attach(socket, message.stream_sid)
            return stream
        return None

    @staticmethod
    def _spend_token(leg: _Leg, presented: str | None) -> bool:
        """Whether the leg's token was presented, spending it if so: it is good for one start."""
        if leg.media_token_spent or presented is None:
            return False
        if not hmac.compare_digest(presented.encode(), leg.media_token.encode()):
            return False
        leg.media_token_spent = True
        return True

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
            # Cancelled counts as failed: a leg left unfinished with no identifier could never be
            # hung up, and would stop the same person being dialled again.
            leg.finished = True
            self._end_stream(call, leg)
            raise
        leg.call_sid = leg.call_sid or call_sid
        if call.released:
            # Released while the dial was being placed, which only a shutdown that could not wait
            # for it does. Nothing is left to hear this leg's callbacks, so it is ended now rather
            # than left to ring into a call that is gone.
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
                (each for each in call.legs.values() if each.call_sid == update.call_sid), None
            )
        if leg is not None and leg.given_up_on:
            # It was on the call after all. Being on it and having left is what happened, and
            # the leg's call is already over, so both are reported at once.
            leg.given_up_on = False
            self._joined(call, leg)
            self._left(call, leg)
            return
        if leg is None or update.sequence <= leg.last_conference or leg.finished:
            return
        leg.last_conference = update.sequence
        leg.call_sid = leg.call_sid or update.call_sid
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
            # A person picked up, and a person who picks up is put in the conference. Only the
            # callbacks saying so were lost. The assistant's leg is not read this way: its
            # application answers it before it has been asked to join anything.
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
        if not assistant.joined or call.conference_sid is None or assistant.call_sid is None:
            return
        users = call.present_users()
        if not users or call.presence is AssistantPresence.STAY:
            target = _Applied()
        elif call.presence is AssistantPresence.LISTEN_ONLY:
            target = _Applied(muted=True)
        else:
            target = _Applied(coach_call_sid=users[-1].call_sid)
        if target == assistant.applied:
            return
        await self._api.update_participant(
            call.conference_sid,
            assistant.call_sid,
            muted=target.muted,
            coach_call_sid=target.coach_call_sid,
        )
        assistant.applied = target

    async def _hang_up_leg(self, call: _Call, leg: _Leg) -> None:
        leg.removed = True
        # A leg is only reachable here once its dial has returned, under the call's lock, and a
        # dial that did not return an identifier finished the leg. Narrowed for the type.
        if leg.call_sid is None:  # pragma: no cover - unreachable while dials hold the lock
            return
        if leg.joined and call.conference_sid is not None:
            await self._api.remove_participant(call.conference_sid, leg.call_sid)
        else:
            await self._api.end_call(leg.call_sid, "completed" if leg.answered else "canceled")

    async def _end_remotely(self, call: _Call) -> None:
        """End the call on the provider's side, whatever state it has reached.

        Every step is tried even when one fails: a refusal to end the conference is no reason to
        leave a user's phone ringing. The first failure is raised once every step has been tried.
        """
        failures: list[ProviderError] = []

        async def attempt(step: Awaitable[object]) -> None:
            try:
                await step
            except ProviderError as failure:
                failures.append(failure)

        conference_sid = call.conference_sid
        if conference_sid is not None:
            await attempt(self._api.end_conference(conference_sid))
        # The conference may never have started, so the caller's own leg is ended too; and a
        # leg still ringing is not in any conference to be ended with it.
        await attempt(self._api.end_call(call.call_id.value, "completed"))
        for leg in list(call.legs.values()):
            if not leg.joined and not leg.finished and not leg.removed:
                await attempt(self._hang_up_leg(call, leg))
        if failures:
            raise failures[0]

    async def _end_unheld(self, call_id: CallId) -> None:
        """End a call this process never held, such as one a stopped process left up.

        Nothing is known of it here but its identifier, which is the caller's leg, and the name
        its conference was given. The caller's leg, the conference and whoever was dialled for the
        call are each ended, each tried whatever became of the others. Already ended is done, not
        a failure, so asking twice is safe.
        """
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
        """End the user's leg a stopped process dialled for this call and left unfinished.

        A leg still ringing is not yet in the conference, so ending the conference leaves it
        ringing, and answering it puts the user in a conference nobody else is in. Nothing records
        its identifier, so it is found by its numbers: dialled from the number this call reached,
        as every leg for a call is, to the line the call was forwarded from, which is the number of
        the only user it could be for (D-033). A call not forwarded was nobody's and dialled no one.
        Any other leg between those two numbers is one a stopped process left too: this runs before
        a process takes calls, and only one process runs against an account.
        """
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
                    # Still ringing when the call ended: answering would join a conference nobody
                    # is in.
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
            if leg.stream is not None:
                await leg.stream.end()

    def _end_stream(self, call: _Call, leg: _Leg) -> None:
        if leg.stream is not None and not leg.stream.has_ended:
            self._spawn(call, leg.stream.end())

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
        # Work a callback started failed where nobody was waiting on it. It is reported as an
        # event, because the orchestrator is the one that can do something about it.
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

    def _leg_by_call_sid(self, call_sid: str) -> tuple[_Call | None, _Leg | None]:
        for call in self._calls.values():
            for leg in call.legs.values():
                if leg.call_sid == call_sid:
                    return call, leg
        return None, None

    def _number_to_call_from(self, called: str | None) -> PhoneNumber:
        dialled = _number_or_none(called)
        return dialled if dialled in self._config.numbers else self._config.numbers[0]

    def _callback_url(self, path: str, call: _Call, leg: _Leg | None = None) -> str:
        query = {CALL_PARAMETER: call.call_id.value}
        if leg is not None:
            query[LEG_PARAMETER] = leg.label
        return self._verifier.url_for(path, urlencode(query))


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
