"""Every tool the agent is given on a call, assembled in one place."""

from __future__ import annotations

from functools import partial
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
    """The tools for one judgement, all writing to its notes."""
    return (
        GetUserPreferences(notes),
        GetCallerContext(notes),
        RequestHumanEscalation(notes),
        TakeAMessage(notes, actions),
        RecordCallOutcome(notes, actions),
        EndCall(notes),
    )


def tools_for_judgements(actions: CallActions) -> ToolsForAJudgement:
    """`tools_for_a_judgement` bound to `actions`, for an agent to call with fresh notes."""
    return partial(tools_for_a_judgement, actions)
