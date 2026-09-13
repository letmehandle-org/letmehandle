"""How calls reach the product, as capabilities core logic asks about, never a vendor (D-004)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime, timedelta

    from letmehandle.domain.models.audio import AudioFormat, AudioFrame
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.identifiers import CallId, EventId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.audio_io import AudioSink, AudioSource


@dataclass(frozen=True, slots=True)
class TransportCapabilities:
    """What a transport genuinely provides, each false unless declared."""

    can_answer_under_program_control: bool = False
    can_screen_before_ringing: bool = False
    can_stream_call_audio_to_ai: bool = False
    can_inject_ai_audio: bool = False
    can_bridge_human: bool = False
    supports_three_way_call: bool = False
    supports_native_ringing: bool = False

    def __post_init__(self) -> None:
        # Injecting the agent's audio without hearing the caller would talk without listening.
        if self.can_inject_ai_audio and not self.can_stream_call_audio_to_ai:
            raise InvariantError(
                "a transport that can inject the agent's audio but cannot stream the "
                "caller's would talk without listening"
            )
        # Three parties cannot be on a call that a third party cannot be added to.
        if self.supports_three_way_call and not self.can_bridge_human:
            raise InvariantError(
                "a transport cannot support a three-way call without being able to add the "
                "third party"
            )

    @property
    def supports_agent_conversation(self) -> bool:
        """Whether the assistant can hold a spoken conversation on this transport."""
        return self.can_stream_call_audio_to_ai and self.can_inject_ai_audio

    def names(self) -> tuple[str, ...]:
        """Every capability, for the audits and the documented matrix."""
        return tuple(field.name for field in fields(self))

    def has(self, capability: str) -> bool:
        if capability not in self.names():
            raise InvariantError(f"{capability!r} is not a transport capability")
        value: bool = getattr(self, capability)
        return value


class ScreeningDecision(StrEnum):
    """What was done with a call before the handset rang, where screening is declared."""

    ALLOW = "allow"
    REJECT = "reject"
    SILENCE = "silence"


class CallEventKind(StrEnum):
    """What happened, in one vocabulary every transport expresses."""

    INCOMING = "incoming"
    ANSWERED = "answered"
    PARTICIPANT_JOINED = "participant_joined"
    PARTICIPANT_LEFT = "participant_left"
    # A leg that was dialled and never joined.
    PARTICIPANT_UNREACHABLE = "participant_unreachable"
    ENDED = "ended"
    FAILED = "failed"


class ParticipantRole(StrEnum):
    """Whom a participant event is about; the caller leaving is reported as `ENDED`."""

    ASSISTANT = "assistant"
    USER = "user"


class ParticipantOutcome(StrEnum):
    """How dialling a participant turned out."""

    ANSWERED = "answered"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    FAILED = "failed"
    ANSWERED_BY_MACHINE = "answered_by_machine"


_PARTICIPANT_KINDS = frozenset(
    {
        CallEventKind.PARTICIPANT_JOINED,
        CallEventKind.PARTICIPANT_LEFT,
        CallEventKind.PARTICIPANT_UNREACHABLE,
    }
)


@dataclass(frozen=True, slots=True)
class CallEvent:
    """Something a transport reports, refusing fields that mean nothing for its kind."""

    kind: CallEventKind
    call_id: CallId
    event_id: EventId
    caller: Caller | None = None
    detail: str | None = None
    participant: ParticipantRole | None = None
    outcome: ParticipantOutcome | None = None
    screening: ScreeningDecision | None = None
    occurred_at: datetime | None = None
    correlation_id: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        # A decision taken before ringing belongs to the event announcing the call.
        if self.screening is not None and self.kind is not CallEventKind.INCOMING:
            raise InvariantError("a screening decision is reported on the incoming event only")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise InvariantError("a reported moment must carry its timezone")
        is_participant_event = self.kind in _PARTICIPANT_KINDS
        if is_participant_event != (self.participant is not None):
            raise InvariantError(
                "a participant event says which participant it is about, and no other event does"
            )
        if self.kind is CallEventKind.PARTICIPANT_UNREACHABLE and (
            self.outcome is None or self.outcome is ParticipantOutcome.ANSWERED
        ):
            raise InvariantError(
                "an unreachable participant carries an outcome other than answered"
            )
        if self.outcome is not None and self.kind not in {
            CallEventKind.PARTICIPANT_JOINED,
            CallEventKind.PARTICIPANT_UNREACHABLE,
        }:
            raise InvariantError("only joining, or failing to, has a dialling outcome")
        if self.kind is CallEventKind.PARTICIPANT_JOINED and self.outcome not in {
            None,
            ParticipantOutcome.ANSWERED,
        }:
            raise InvariantError("a participant who joined was answered")
        if self.outcome is ParticipantOutcome.ANSWERED_BY_MACHINE and (
            self.participant is not ParticipantRole.USER
        ):
            raise InvariantError("only a person's phone can be answered by a machine")


class AssistantPresence(StrEnum):
    """What the assistant does while the user is on the call; `LEAVE` is final."""

    STAY = "stay"
    LISTEN_ONLY = "listen_only"
    SPEAK_TO_USER_ONLY = "speak_to_user_only"
    LEAVE = "leave"


class CallTransport(ABC):
    """The port calls arrive through; operations needing a capability are reached by narrowing."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this transport is called. For logs and metrics, never for a decision."""

    @property
    @abstractmethod
    def capabilities(self) -> TransportCapabilities:
        """What it can do."""

    @abstractmethod
    def events(self) -> AsyncIterator[CallEvent]:
        """Everything happening on this transport's calls."""

    @abstractmethod
    async def terminate(self, call_id: CallId) -> None:
        """Release all this transport holds for the call, ending it where it can; safe to repeat."""

    def require(self, capability: str) -> None:
        """Raise unless the capability is declared."""
        if not self.capabilities.has(capability):
            raise CapabilityNotSupportedError(self.name, capability)


@runtime_checkable
class SupportsAnswering(Protocol):
    """A transport that can put the assistant on a call, however it gets there (D-027)."""

    async def answer(self, call_id: CallId) -> None:
        """Put the assistant on the call. Changes nothing while the assistant is already on it."""


@runtime_checkable
class SupportsScreening(Protocol):
    """A transport whose handset decides a call before it rings, reporting the decision (D-028)."""

    def screening_decisions(self) -> frozenset[ScreeningDecision]:
        """Which decisions this transport can apply. Always includes letting the call ring."""

    def screening_deadline(self) -> timedelta:
        """How long the platform allows for a decision before it rings regardless."""


@runtime_checkable
class SupportsAudioStreaming(Protocol):
    """A transport that can carry the call's audio to and from the assistant."""

    # A plain `def` that returns the iterator, so nobody awaits before iterating.
    def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        """The caller's audio, as it arrives."""

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        """Put the assistant's audio onto the call."""

    def audio_format(self) -> AudioFormat:
        """The format this transport speaks, so the speech adapter can convert at its edge."""

    def audio_source(self, call_id: CallId) -> AudioSource:
        """The caller's audio as a conversation's source."""

    def audio_sink(self, call_id: CallId) -> AudioSink:
        """Where the assistant's voice goes on the call, a sink whose `discard` reaches the line."""


@runtime_checkable
class SupportsBridging(Protocol):
    """A transport that can add a third party to a call already in progress."""

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        """Dial the number and join them to this call. Not a transfer: nobody is dropped."""

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        """Take them off it, leaving the call standing."""


@runtime_checkable
class SupportsThreeWayCall(Protocol):
    """A transport on which the caller, the assistant and the user can all be at once."""

    async def set_assistant_presence(self, call_id: CallId, presence: AssistantPresence) -> None:
        """Choose what the assistant does while the user is on the call; illegal after `LEAVE`."""


def answering(transport: CallTransport) -> SupportsAnswering:
    """Narrow to a transport that can take a call itself."""
    transport.require("can_answer_under_program_control")
    if not isinstance(transport, SupportsAnswering):
        raise CapabilityNotSupportedError(transport.name, "can_answer_under_program_control")
    return transport


def screening(transport: CallTransport) -> SupportsScreening:
    """Narrow to a transport that can screen, or raise saying which capability is missing."""
    transport.require("can_screen_before_ringing")
    if not isinstance(transport, SupportsScreening):
        raise CapabilityNotSupportedError(transport.name, "can_screen_before_ringing")
    return transport


def audio_streaming(transport: CallTransport) -> SupportsAudioStreaming:
    """Narrow to a transport that can carry call audio both ways."""
    transport.require("can_stream_call_audio_to_ai")
    transport.require("can_inject_ai_audio")
    if not isinstance(transport, SupportsAudioStreaming):
        raise CapabilityNotSupportedError(transport.name, "can_stream_call_audio_to_ai")
    return transport


def bridging(transport: CallTransport) -> SupportsBridging:
    """Narrow to a transport that can add a third party to a live call."""
    transport.require("can_bridge_human")
    if not isinstance(transport, SupportsBridging):
        raise CapabilityNotSupportedError(transport.name, "can_bridge_human")
    return transport


def three_way(transport: CallTransport) -> SupportsThreeWayCall:
    """Narrow to a transport that can hold the caller, the assistant and the user at once."""
    transport.require("supports_three_way_call")
    if not isinstance(transport, SupportsThreeWayCall):
        raise CapabilityNotSupportedError(transport.name, "supports_three_way_call")
    return transport
