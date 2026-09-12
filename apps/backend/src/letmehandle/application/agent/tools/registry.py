"""Every tool the agent is given on a call, assembled in one place.

The adapter asks for the set and presents it; it never picks tools or wires their dependencies.
A tool that exists but is missing from here is a tool no model can reach, and a tool an adapter
built for itself is one whose checks nobody reviewed in this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.application.agent.tools.caller import GetCallerContext
from letmehandle.application.agent.tools.end_call import EndCall
from letmehandle.application.agent.tools.escalation import RequestHumanEscalation
from letmehandle.application.agent.tools.message import TakeAMessage
from letmehandle.application.agent.tools.outcome import RecordCallOutcome
from letmehandle.application.agent.tools.preferences import GetUserPreferences

if TYPE_CHECKING:
    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.agent.tool import AgentTool, ToolsForAJudgement


def tools_for_a_judgement(actions: CallActions, notes: JudgementNotes) -> tuple[AgentTool, ...]:
    """The tools for one judgement, all writing to the same notes, which are new for each judgement.

    Only the tools that keep something for the user are handed `actions`. Asking for the user and
    asking to end the call are written to `notes`, for the conclusion to act on once the model has
    finished.
    """
    return (
        GetUserPreferences(notes),
        GetCallerContext(notes),
        RequestHumanEscalation(notes),
        TakeAMessage(notes, actions),
        RecordCallOutcome(notes, actions),
        EndCall(notes),
    )


def tools_for_judgements(actions: CallActions) -> ToolsForAJudgement:
    """`tools_for_a_judgement`, ready for an agent to call once per judgement with fresh notes."""

    def for_one(notes: JudgementNotes) -> tuple[AgentTool, ...]:
        return tools_for_a_judgement(actions, notes)

    return for_one
