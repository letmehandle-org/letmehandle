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
    from letmehandle.application.agent.escalation import EscalationService
    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.agent.tool import AgentTool


def tools_for_a_judgement(
    actions: CallActions, escalation: EscalationService, notes: JudgementNotes
) -> tuple[AgentTool, ...]:
    """The tools for one judgement, all writing to the same notes.

    `escalation` outlives the judgement — it is what remembers a call was already escalated — and
    must be the one the adapter's own end-of-turn check uses, or the call has two memories of
    whether the user's phone rang. `notes` is new for each judgement.
    """
    return (
        GetUserPreferences(notes),
        GetCallerContext(notes),
        RequestHumanEscalation(notes, escalation),
        TakeAMessage(notes, actions),
        RecordCallOutcome(notes, actions),
        EndCall(notes, actions, escalation),
    )
