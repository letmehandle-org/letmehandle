"""One judgement's worth of tools, with everything they touch left out where a test can count it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.application.agent.escalation import EscalationService
from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import ToolRefusal
from letmehandle.application.agent.tool import ToolResult
from tests.support.recording_call_actions import RecordingCallActions

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import AgentTool, ToolOutcome


@dataclass
class Kit:
    """The actions, the escalation memory and the notes a set of tools shares."""

    actions: RecordingCallActions = field(default_factory=RecordingCallActions)
    notes: JudgementNotes = field(default_factory=JudgementNotes)
    escalation: EscalationService = field(init=False)

    def __post_init__(self) -> None:
        self.escalation = EscalationService(self.actions)


async def refused(tool: AgentTool, call: CallSoFar, arguments: Mapping[str, object]) -> str:
    """Invoke, insist on a refusal from this tool, and return its reason."""
    outcome = await tool.invoke(call, arguments)
    assert isinstance(outcome, ToolRefusal), outcome
    assert outcome.tool == tool.spec.name
    return outcome.reason


async def answered(tool: AgentTool, call: CallSoFar, arguments: Mapping[str, object]) -> str:
    """Invoke, insist the tool acted, and return what it said."""
    outcome: ToolOutcome = await tool.invoke(call, arguments)
    assert isinstance(outcome, ToolResult), outcome
    return outcome.content
