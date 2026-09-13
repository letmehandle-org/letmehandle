"""Everything a call's run can be told, as values in its inbox (D-029)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from letmehandle.application.agent.ports import AgentJudgement, CallEnding, OutcomeRecord
    from letmehandle.application.orchestration.plan import DialTheUser
    from letmehandle.application.speech.conversation import ConversationEnd, TranscriptTurn
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.policy.escalation import EscalationProposal
    from letmehandle.domain.ports.call_transport import CallEvent


@dataclass(frozen=True, slots=True)
class Reported:
    """The transport reported something about the call."""

    event: CallEvent


@dataclass(frozen=True, slots=True)
class Heard:
    """Somebody said something, and the conversation settled it."""

    turn: TranscriptTurn


@dataclass(frozen=True, slots=True)
class ConversationStopped:
    """The conversation ended: which side ended it, or None when it failed."""

    end: ConversationEnd | None


@dataclass(frozen=True, slots=True)
class Judged:
    """One look at the call finished: its judgement, or None when it failed or ran out of time."""

    judgement: AgentJudgement | None


@dataclass(frozen=True, slots=True)
class RingRanOut:
    """The user's phone rang as long as it may; `generation` names the ring that ran out."""

    dial: DialTheUser
    generation: int


@dataclass(frozen=True, slots=True)
class SilenceRanOut:
    """The call's audio stopped, and the transport did not say the call ended in time."""

    generation: int


@dataclass(frozen=True, slots=True)
class CallRanTooLong:
    """The call has lasted as long as any call may, and nothing reported it ending."""

    generation: int


@dataclass(frozen=True, slots=True)
class Abandoned:
    """The call ends now, whatever it was doing: the process stops, or its account is deleted."""


@dataclass(frozen=True, slots=True)
class EscalationRequested:
    decision: EscalationDecision
    reply: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class EndingRequested:
    ending: CallEnding
    assessment: EscalationProposal
    reply: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class OutcomeRecorded:
    record: OutcomeRecord
    reply: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class MessageTaken:
    message: str
    reply: asyncio.Future[None]


type Request = EscalationRequested | EndingRequested | OutcomeRecorded | MessageTaken
type Input = (
    Reported
    | Heard
    | ConversationStopped
    | Judged
    | RingRanOut
    | SilenceRanOut
    | CallRanTooLong
    | Abandoned
    | Request
)
