"""What can happen to a call, decided from the transport before anything happens to it.

A step the transport cannot perform is never built. There is no escalation step on a transport that
cannot add the user to a call, and no assistant step on one that cannot answer and carry audio, so
code that would escalate or converse there has nothing to call: the path does not exist, which is
stronger than a check that refuses it (D-029).

Each step holds the narrowed transport it acts through. Narrowing is the only way to reach an
operation behind a capability, so a step's existence is the proof that its operation is there.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.ports.call_transport import (
    ScreeningDecision,
    answering,
    audio_streaming,
    bridging,
)

if TYPE_CHECKING:
    from letmehandle.application.orchestration.ports import Assistance
    from letmehandle.domain.ports.call_transport import (
        CallEvent,
        CallTransport,
        SupportsAnswering,
        SupportsAudioStreaming,
        SupportsBridging,
    )


@dataclass(frozen=True, slots=True)
class Converse:
    """The assistant takes the call and talks on it, with the services it speaks and judges with."""

    answering: SupportsAnswering
    audio: SupportsAudioStreaming
    assistance: Assistance


@dataclass(frozen=True, slots=True)
class DialTheUser:
    """The user's phone is dialled and they join the call where the caller already is."""

    bridge: SupportsBridging


@dataclass(frozen=True, slots=True)
class LetItRing:
    """The call rings where it already is, and whoever holds that phone answers it."""


@dataclass(frozen=True, slots=True)
class CallPlan:
    """The steps one call may take.

    `put_through` is how the call reaches the user without the assistant, if it can. `assistant`
    and `escalation` exist only together with what they need: escalation is the assistant asking
    for the user, so there is none without an assistant. `screened` is the decision a handset
    already applied before it rang; a call carrying one is recorded, never routed again.
    """

    put_through: DialTheUser | LetItRing | None
    assistant: Converse | None
    escalation: DialTheUser | None
    screened: ScreeningDecision | None


def plan_for(
    transport: CallTransport, incoming: CallEvent, assistance: Assistance | None
) -> CallPlan:
    """The plan for the call `incoming` announces, from what `transport` declares it can do.

    `assistance` is what an assistant would speak with; a deployment without it offers no assistant
    step, and the orchestrator refuses to be built that way over a transport that could hold one.
    """
    capabilities = transport.capabilities
    assistant = (
        Converse(answering(transport), audio_streaming(transport), assistance)
        if assistance is not None
        and capabilities.supports_agent_conversation
        and capabilities.can_answer_under_program_control
        else None
    )
    dial = DialTheUser(bridging(transport)) if capabilities.can_bridge_human else None
    ring = LetItRing() if capabilities.supports_native_ringing else None
    return CallPlan(
        put_through=dial or ring,
        assistant=assistant,
        escalation=dial if assistant is not None else None,
        # A screening transport that reported no decision let the call ring: that is what every
        # failure to decide does on a handset (D-028).
        screened=(
            (incoming.screening or ScreeningDecision.ALLOW)
            if capabilities.can_screen_before_ringing
            else None
        ),
    )
