"""The agent's ports: the judgement asked for, and the actions its tools take (D-026)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from letmehandle.application.preferences.context import build_preference_context
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from letmehandle.application.preferences.context import PreferenceContext
    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.preferences import CallRules, UserPreferences
    from letmehandle.domain.models.summary import CallOutcome, ExtractedDetail
    from letmehandle.domain.policy.escalation import EscalationProposal


@dataclass(frozen=True, slots=True)
class CallSoFar:
    """What the agent may know about one call at one moment; the transcript is data."""

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

    @classmethod
    def for_user(
        cls,
        *,
        call_id: CallId,
        preferences: UserPreferences,
        caller: Caller,
        transcript: Iterable[TranscriptEntry],
        now: datetime,
    ) -> CallSoFar:
        """The call as one user's preferences see it at `now`."""
        contact = next(
            (
                each
                for each in preferences.important_contacts
                if caller.number is not None and each.number == caller.number
            ),
            None,
        )
        return cls(
            call_id=call_id,
            caller=caller,
            transcript=tuple(transcript),
            preferences=build_preference_context(preferences, now=now),
            authority=preferences.authority,
            rules=preferences.rules,
            from_important_contact=contact is not None,
            now=now,
            contact_label=contact.label if contact is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ToolRefusal:
    """A tool declined to act, and why; returned to the model and recorded for the user."""

    tool: str
    reason: str

    def __post_init__(self) -> None:
        if not self.tool.strip() or not self.reason.strip():
            raise InvariantError("a refusal names the tool and says why")


@dataclass(frozen=True, slots=True)
class AgentJudgement:
    """The final reading, the policy's escalation, the refusals and whether the call ended."""

    proposal: EscalationProposal
    escalation: EscalationDecision
    refusals: tuple[ToolRefusal, ...] = field(default_factory=tuple)
    ended: bool = False


class ConsiderEscalation(Protocol):
    """Decides whether the user is needed and reaches them at most once per urgency."""

    async def consider(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """The policy's decision on `proposal`, with the user reached if nobody has yet."""


class CallAgent(ABC):
    """Judges a call so far, using the tools it is given."""

    @abstractmethod
    async def judge(self, call: CallSoFar) -> AgentJudgement:
        """Judges the call; a misbehaving model yields the not-understood fallback."""


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
    """The kind of ending a call the assistant hangs up on had."""

    RESOLVED = "resolved"
    HANDED_OVER = "handed_over"
    DECLINED = "declined"


class CallActions(ABC):
    """The only ways a judgement can affect a call. Implemented by orchestration."""

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
    async def end_call(
        self, call_id: CallId, ending: CallEnding, assessment: EscalationProposal
    ) -> None:
        """Hangs up on the caller, with the judgement's reading of the call it ends."""
