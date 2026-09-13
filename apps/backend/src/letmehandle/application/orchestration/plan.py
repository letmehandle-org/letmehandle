"""What a call may do, built only from what its transport can do (D-029)."""

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
    """The steps one call may take; `screened` is a decision its handset already applied."""

    put_through: DialTheUser | LetItRing | None
    assistant: Converse | None
    escalation: DialTheUser | None
    screened: ScreeningDecision | None


def plan_for(
    transport: CallTransport, incoming: CallEvent, assistance: Assistance | None
) -> CallPlan:
    """The plan for the call `incoming` announces, from `transport`'s capabilities."""
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
        # A screening transport that reported no decision let the call ring (D-028).
        screened=(
            (incoming.screening or ScreeningDecision.ALLOW)
            if capabilities.can_screen_before_ringing
            else None
        ),
    )
