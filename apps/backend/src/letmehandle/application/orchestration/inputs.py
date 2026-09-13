"""Everything a call's run can be told, as values in its inbox.

Transport events, the agent's requests, a conversation stopping, a judgement finishing, a wait
running out and the process stopping all arrive the same way, and are handled one at a time by the
run alone (D-029). A request that someone is waiting on carries a future the run settles once it
has acted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

    from letmehandle.application.agent.ports import AgentJudgement, CallEnding, OutcomeRecord
    from letmehandle.application.orchestration.plan import DialTheUser
    from letmehandle.application.speech.conversation import ConversationEnd, TranscriptTurn
    from letmehandle.domain.models.escalation import EscalationDecision
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
    """The user's phone rang for as long as it may. `dial` is the step that is ringing it.

    `generation` tells the ring still armed from one since cancelled whose expiry was already on its
    way to the inbox.
    """

    dial: DialTheUser
    generation: int


@dataclass(frozen=True, slots=True)
class SilenceRanOut:
    """The call's audio stopped, and the transport did not say the call ended in time."""

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
    | Abandoned
    | Request
)
