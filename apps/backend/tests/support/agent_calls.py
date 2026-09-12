"""Calls, tools and the escalation check, for exercising the agent without orchestration.

The tools here are small but not hollow. A guarded tool checks the user's grant before it acts and
does nothing when it refuses, which is the one behaviour every real tool must share, so a test that
passes against it has exercised the refusal path rather than a stand-in that always says yes.

The escalation check is the real policy. Only the call is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import CallSoFar, ToolRefusal
from letmehandle.application.agent.tool import AgentTool, ToolResult, ToolSpec
from letmehandle.application.preferences.context import build_preference_context
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.call import Speaker, TranscriptEntry
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.preferences import CallRules, UserPreferences
from letmehandle.domain.policy.escalation import CallCircumstances, decide_escalation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.tool import ToolOutcome
    from letmehandle.domain.models.authority import Capability
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.policy.escalation import EscalationProposal

# Midday on a weekday, in no quiet hours anybody has set.
MIDDAY: Final = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)


def a_call(
    *said: str,
    authority: AgentAuthority | None = None,
    escalate_at_or_above: CallImportance = CallImportance.NOTABLE,
    from_important_contact: bool = False,
    locale: str = "en",
) -> CallSoFar:
    """A call in which the caller has said each of `said`, in turn."""
    granted = authority or AgentAuthority.none()
    rules = CallRules(escalate_at_or_above=escalate_at_or_above)
    preferences = UserPreferences(rules=rules, authority=granted, locale=locale)
    return CallSoFar(
        call_id=CallId("call-1"),
        caller=Caller(),
        transcript=tuple(TranscriptEntry(Speaker.CALLER, text, MIDDAY) for text in said),
        preferences=build_preference_context(preferences, now=MIDDAY),
        authority=granted,
        rules=rules,
        from_important_contact=from_important_contact,
        now=MIDDAY,
    )


async def decide_by_policy(call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
    """The escalation check, as the real policy makes it."""
    return decide_escalation(
        proposal,
        CallCircumstances(
            rules=call.rules,
            authority=call.authority,
            now=call.now,
            from_important_contact=call.from_important_contact,
        ),
    )


_NO_ARGUMENTS: Final[Mapping[str, object]] = {"type": "object", "properties": {}}


@dataclass
class GuardedTool(AgentTool):
    """Does one thing the user must have granted, and records it only when it was allowed."""

    name: str
    capability: Capability | None = None
    description: str = "Does what its name says."
    reply: str = "done"
    acted: list[Mapping[str, object]] = field(default_factory=list)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, _NO_ARGUMENTS)

    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        if self.capability is not None and not call.authority.allows(self.capability):
            return ToolRefusal(self.name, f"the user has not allowed {self.capability.value}")
        self.acted.append(dict(arguments))
        return ToolResult(self.reply)


@dataclass
class BrokenTool(AgentTool):
    """Raises whenever it is used, the way a tool with a defect does."""

    error: Exception
    name: str = "broken"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, "A tool that fails.", _NO_ARGUMENTS)

    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        raise self.error
