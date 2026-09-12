"""How a call exists at all.

Named for the concern rather than for a vendor, because a programmable telephony account and a
platform's own call screening service are the same thing served two ways — and they differ in
kind, not only in supplier. One can screen a call before the handset rings but cannot hand an
application the audio of a call; the other can stream that audio and add a second person to a
call already in progress, but never sees the call before it connects.

That is why capabilities exist, and why nothing outside bootstrap is allowed to know which
transport it has. Core logic asks what is available, never who is providing it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from letmehandle.domain.errors import CapabilityNotSupportedError, InvariantError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.models.audio import AudioFormat, AudioFrame
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.identifiers import CallId, EventId
    from letmehandle.domain.models.phone_number import PhoneNumber


@dataclass(frozen=True, slots=True)
class TransportCapabilities:
    """What a transport can actually do.

    Every field defaults to false. A transport that forgets to declare something offers less
    than it could, and that is recoverable; one that inherits a true it did not mean promises
    a caller something that will fail while they are on the line.

    A transport must declare only what the platform genuinely provides. Android's call
    screening does not give an application the audio of a call, so a transport built on it
    declares `can_stream_call_audio_to_ai` false — and the product then offers no spoken
    assistant on that path, rather than offering one that cannot work.
    """

    can_screen_before_ringing: bool = False
    can_stream_call_audio_to_ai: bool = False
    can_inject_ai_audio: bool = False
    can_bridge_human: bool = False
    supports_three_way_call: bool = False
    supports_native_ringing: bool = False

    def __post_init__(self) -> None:
        # A transport that can put the agent's voice on the call but cannot hear the caller is
        # not a conversation, it is an announcement. Declaring that pair would produce an
        # assistant talking into a line it cannot hear.
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
        """Whether the assistant can hold a spoken conversation on this transport.

        The question the orchestrator actually asks. Written once here rather than as two
        flags checked together in several places, where one of them eventually gets forgotten.
        """
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
    """What to do with a call before the handset rings.

    Only available where `can_screen_before_ringing` is declared. `SILENCE` is distinct from
    `REJECT` because they mean different things to the caller: one rings out, the other is
    refused, and a user choosing between them is choosing what the caller learns.
    """

    ALLOW = "allow"
    REJECT = "reject"
    SILENCE = "silence"


class CallEventKind(StrEnum):
    """What happened, in terms every transport can express.

    Deliberately the same vocabulary for both transports, so the orchestrator sees one set of
    events regardless of what produced them.
    """

    INCOMING = "incoming"
    ANSWERED = "answered"
    PARTICIPANT_JOINED = "participant_joined"
    PARTICIPANT_LEFT = "participant_left"
    ENDED = "ended"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CallEvent:
    """Something a transport reports.

    `event_id` is the provider's own, and it is what makes idempotency possible. Providers
    redeliver; the only reliable way to recognise a repeat is the identifier the provider
    assigned to it, not a heuristic over the contents.
    """

    kind: CallEventKind
    call_id: CallId
    event_id: EventId
    caller: Caller | None = None
    detail: str | None = None


class CallTransport(ABC):
    """The port by which calls reach the product.

    Implementations declare their capabilities honestly and implement only what they declare.
    The operations every transport must support are on this class; the ones that depend on a
    capability are on the protocols below, reached by narrowing through `screening`,
    `audio_streaming` and `bridging`. A caller that has not narrowed cannot name those methods,
    which is the static half of the guarantee; the narrowing functions are the runtime half,
    and they catch a transport whose declaration and implementation disagree.
    """

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
    async def answer(self, call_id: CallId) -> None:
        """Take the call."""

    @abstractmethod
    async def terminate(self, call_id: CallId) -> None:
        """End it, and release everything holding it open. Safe to call more than once."""

    def require(self, capability: str) -> None:
        """Raise unless the capability is declared.

        The runtime half of the guarantee. The static half is the narrowing below: a caller
        that has not narrowed cannot name the method at all, so this catches the case where a
        transport's declaration and its implementation disagree.
        """
        if not self.capabilities.has(capability):
            raise CapabilityNotSupportedError(self.name, capability)


@runtime_checkable
class SupportsScreening(Protocol):
    """A transport that sees a call before the handset rings."""

    async def screen(self, call_id: CallId, decision: ScreeningDecision) -> None:
        """Allow, reject or silence the call, within the platform's deadline."""


@runtime_checkable
class SupportsAudioStreaming(Protocol):
    """A transport that can carry the call's audio to and from the assistant."""

    # Not `async def`: this returns the iterator, it is not awaited for one. Declared the
    # other way, every implementer writes an async generator and every caller has to await
    # before iterating, which reads as a mistake because it is one.
    def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        """The caller's audio, as it arrives."""

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        """Put the assistant's audio onto the call."""

    def audio_format(self) -> AudioFormat:
        """The format this transport speaks, so the speech adapter can convert at its edge."""


@runtime_checkable
class SupportsBridging(Protocol):
    """A transport that can add a third party to a call already in progress.

    The capability the product's central promise rests on: the caller stays where they are and
    the user joins them. A transport without it cannot produce that experience, and the
    orchestrator must offer something else rather than pretend.
    """

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        """Dial the number and join them to this call. Not a transfer: nobody is dropped."""

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        """Take them off it, leaving the call standing."""


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
