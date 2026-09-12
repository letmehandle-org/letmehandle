"""What the agent is asked, what it may do, and what it answers.

Two ports, facing opposite ways. `CallAgent` is what orchestration asks for a judgement on the call
so far; an implementation runs a model and the tools below. `CallActions` is what those tools use
to affect the call; orchestration implements it, because only orchestration holds the call.

Beside `CallAgent` sits `ConsiderEscalation`, the one path by which a call reaches the user. An
agent is handed it for its end-of-turn check, and the escalation tool is handed the same instance,
so a call has one memory of whether the user's phone has already rung.

Neither names a framework or a model. D-026 puts the SDK in an adapter behind `CallAgent`, so that
everything a user relies on — what the assistant may do, when the user's phone rings — lives here
and in the domain, where it can be read and tested without one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.application.preferences.context import PreferenceContext
    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.preferences import CallRules
    from letmehandle.domain.models.summary import CallOutcome, ExtractedDetail
    from letmehandle.domain.policy.escalation import EscalationProposal


@dataclass(frozen=True, slots=True)
class CallSoFar:
    """Everything the agent may know about one call at one moment.

    The transcript is data, never instruction. Every word in it arrived from a caller nobody has
    verified, and the one who most wants the assistant to do something it should not is the one
    speaking.

    `contact_label` is what the user called this caller in their important contacts, and is set
    only for a caller who is one. It is never taken from anything that arrived with the call: a
    caller's display name is text a stranger or their network chose.
    """

    call_id: CallId
    caller: Caller
    transcript: tuple[TranscriptEntry, ...]
    preferences: PreferenceContext
    authority: AgentAuthority
    rules: CallRules
    from_important_contact: bool
    now: datetime
    contact_label: str | None = None

    def __post_init__(self) -> None:
        if self.contact_label is not None and not self.from_important_contact:
            raise InvariantError("only an important contact carries the label the user gave them")
        if self.contact_label is not None and not self.contact_label.strip():
            raise InvariantError("a contact label is either absent or has something in it")


@dataclass(frozen=True, slots=True)
class ToolRefusal:
    """A tool declined to act, and why.

    Returned rather than raised, so the agent can say something sensible to the caller, and
    recorded, so the user can see what was asked of their assistant that it was not allowed to do.
    """

    tool: str
    reason: str

    def __post_init__(self) -> None:
        if not self.tool.strip() or not self.reason.strip():
            raise InvariantError("a refusal names the tool and says why")


@dataclass(frozen=True, slots=True)
class AgentJudgement:
    """What the agent concluded from one look at the call.

    `proposal` is its reading of the call, which the escalation policy — not the agent — turns into
    a decision. `refusals` are the actions it attempted and was not allowed, in order.
    """

    proposal: EscalationProposal
    escalation: EscalationDecision
    refusals: tuple[ToolRefusal, ...] = field(default_factory=tuple)
    ended: bool = False


class ConsiderEscalation(Protocol):
    """Whether the user is needed on a call, decided by the policy and acted on at most once.

    Implemented by `application.agent.escalation.EscalationService`. An agent and the tools it is
    given must share one instance: two would each think the other had not rung the user.
    """

    async def consider(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """The policy's decision on `proposal`, with the user reached if nobody has yet."""


class CallAgent(ABC):
    """Judges a call so far, using the tools it is given."""

    @abstractmethod
    async def judge(self, call: CallSoFar) -> AgentJudgement:
        """Look at the call and decide what to do next.

        Never raises for a model that misbehaves: an unreadable, truncated or invalid answer
        produces the defined fallback — a proposal that the caller could not be understood, which
        the policy then decides on — rather than a crash or a value quietly made up.
        """


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    """What the agent writes down about a call it handled."""

    outcome: CallOutcome
    headline: str
    details: tuple[ExtractedDetail, ...] = ()

    def __post_init__(self) -> None:
        if not self.headline.strip():
            raise InvariantError("an outcome with no headline tells the user nothing")


class CallEnding(StrEnum):
    """Which kind of ending a call the assistant hangs up on had.

    A fixed set rather than prose, so orchestration and the call history receive something they can
    act on, and no caller's words travel with it.
    """

    RESOLVED = "resolved"
    HANDED_OVER = "handed_over"
    DECLINED = "declined"


class CallActions(ABC):
    """The only ways the agent's tools can affect a call. Implemented by orchestration."""

    @abstractmethod
    async def escalate(self, call_id: CallId, decision: EscalationDecision) -> None:
        """Reach the user, as the policy decided. Only ever called with a decision to escalate."""

    @abstractmethod
    async def record_outcome(self, call_id: CallId, record: OutcomeRecord) -> None:
        """Keep what the agent concluded about the call."""

    @abstractmethod
    async def take_message(self, call_id: CallId, message: str) -> None:
        """Keep a message the caller left for the user."""

    @abstractmethod
    async def end_call(self, call_id: CallId, ending: CallEnding) -> None:
        """Hang up on the caller, for the kind of ending the tool was allowed."""
