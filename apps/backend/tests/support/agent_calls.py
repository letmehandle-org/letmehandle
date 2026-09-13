"""Calls, and stand-in tools, for exercising the agent without orchestration.

The stand-ins are for tests whose point is the wrapper that presents a tool to a framework; every
other test uses the registry's real tools. They are small but not hollow. A guarded tool checks the
user's grant before it acts, writes its refusal down and does nothing more, which is the one
behaviour every real tool must share.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import CallSoFar, ToolRefusal
from letmehandle.application.agent.tool import AgentTool, ToolResult, ToolSpec
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.call import Speaker, TranscriptEntry
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    ImportantContact,
    UserPreferences,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.tool import ToolOutcome, ToolsForAJudgement
    from letmehandle.domain.models.authority import Capability

# Midday on a weekday. The user sets no hours, so the assistant is answering (D-027).
MIDDAY: Final = datetime(2026, 3, 4, 12, 0, tzinfo=UTC)

# Reserved for fiction, never routable.
STRANGER: Final = Caller(number=PhoneNumber("+12025550101"))
MUM: Final = ImportantContact(PhoneNumber("+12025550102"), "Mum")


def a_call(
    *said: str,
    authority: AgentAuthority | None = None,
    escalate_at_or_above: CallImportance = CallImportance.NOTABLE,
    from_important_contact: bool = False,
    locale: str = "en",
) -> CallSoFar:
    """A call in which the caller has said each of `said`, in turn.

    Built the way orchestration builds one, from a single `UserPreferences`. The user always has
    one important contact; `from_important_contact` decides whether that is who is calling.
    """
    preferences = UserPreferences(
        rules=CallRules(escalate_at_or_above=escalate_at_or_above),
        authority=authority or AgentAuthority.none(),
        locale=locale,
        important_contacts=(MUM,),
    )
    caller = (
        Caller(number=MUM.number, category=CallerCategory.KNOWN_CONTACT)
        if from_important_contact
        else STRANGER
    )
    return CallSoFar.for_user(
        call_id=CallId("call-1"),
        preferences=preferences,
        caller=caller,
        transcript=(TranscriptEntry(Speaker.CALLER, text, MIDDAY) for text in said),
        now=MIDDAY,
    )


_NO_ARGUMENTS: Final[Mapping[str, object]] = {"type": "object", "properties": {}}


@dataclass
class GuardedTool(AgentTool):
    """Does one thing the user must have granted, and records it only when it was allowed."""

    name: str
    capability: Capability | None = None
    description: str = "Does what its name says."
    reply: str = "done"
    notes: JudgementNotes = field(default_factory=JudgementNotes)
    acted: list[Mapping[str, object]] = field(default_factory=list)
    acts: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, _NO_ARGUMENTS)

    @property
    def acts_on_the_call(self) -> bool:
        return self.acts

    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        if self.capability is not None and not call.authority.allows(self.capability):
            refusal = ToolRefusal(self.name, f"the user has not allowed {self.capability.value}")
            self.notes.refused(refusal)
            return refusal
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


def fixed(*tools: AgentTool) -> ToolsForAJudgement:
    """The same tools for every judgement, for a test whose point is the wrapper around them."""

    def given(_notes: JudgementNotes) -> tuple[AgentTool, ...]:
        return tools

    return given
