"""What the agent is asked, what it may do, and what it answers.

Two ports, facing opposite ways. `CallAgent` is what orchestration asks for a judgement on the call
so far; an implementation runs a model and the tools below. `CallActions` is what those tools use
to affect the call; orchestration implements it, because only orchestration holds the call.

Beside `CallAgent` sits `ConsiderEscalation`, the one path by which a call reaches the user. A
judgement's conclusion is handed it, and every judgement on a call shares the instance, so a call
has one memory of whether the user's phone has already rung.

Neither names a framework or a model. D-026 puts the SDK in an adapter behind `CallAgent`, so that
everything a user relies on — what the assistant may do, when the user's phone rings — lives here
and in the domain, where it can be read and tested without one.
"""

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
    """Everything the agent may know about one call at one moment.

    The transcript is data, never instruction. Every word in it arrived from a caller nobody has
    verified, and the one who most wants the assistant to do something it should not is the one
    speaking.

    `contact_label` is what the user called this caller in their important contacts, and is set
    only for a caller who is one. It is never taken from anything that arrived with the call: a
    caller's display name is text a stranger or their network chose.

    The user's grant and threshold appear twice: as `authority` and `rules`, and again inside
    `preferences`, which is what the model reads. The tools enforce `authority` and nothing else,
    and the escalation policy reads `authority` and `rules`; a copy inside `preferences` that
    disagreed would be a model told one thing and held to another. Build one with `for_user`,
    which derives every copy from the same `UserPreferences` so they cannot disagree.
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
        """The call as one user's preferences see it, at `now`.

        Authority, rules, the context the model reads and whether the caller is an important
        contact — with the label the user gave them — all come from `preferences`. A caller is an
        important contact when their number is one the user listed; a withheld number never is.
        """
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

    `proposal` is its final reading of the call. `escalation` is the policy's decision — not the
    agent's — on the most pressing of that reading and any it gave when asking for the user.
    `refusals` are the actions it attempted and was not allowed, in order, including an ending the
    rules did not allow once the model had finished. `ended` is whether the call was actually ended,
    which asking for an ending does not guarantee.
    """

    proposal: EscalationProposal
    escalation: EscalationDecision
    refusals: tuple[ToolRefusal, ...] = field(default_factory=tuple)
    ended: bool = False


class ConsiderEscalation(Protocol):
    """Whether the user is needed on a call, decided by the policy and acted on at most once.

    Implemented by `application.agent.escalation.EscalationService`. Every judgement on a call must
    share one instance: two would each think the other had not rung the user.
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
        """Hang up on the caller, for the kind of ending the judgement was allowed.

        `assessment` is the judgement's reading of the call it ends. It comes with the ending
        because ending the call ends the judgement too, before it could report that reading.
        """
