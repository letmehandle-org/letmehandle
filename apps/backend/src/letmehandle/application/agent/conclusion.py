"""Acts once on a finished judgement: the most pressing escalation first, then an allowed ending."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.escalation import circumstances_of
from letmehandle.application.agent.ports import AgentJudgement, CallEnding, ToolRefusal
from letmehandle.application.agent.tools.end_call import END_CALL
from letmehandle.domain.policy.escalation import most_pressing

if TYPE_CHECKING:
    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions, CallSoFar, ConsiderEscalation
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.policy.escalation import EscalationProposal

# Why a requested ending is withheld when the judgement itself did not finish well.
NOT_ENDED_AFTER_A_FAILURE: Final = "an action on the call failed, so the call was not ended"
NOT_ENDED_WITHOUT_AN_ASSESSMENT: Final = (
    "the call was not assessed, so it was not ended on a judgement that never finished"
)


class JudgementConclusion:
    """Escalates, then ends the call if that is still allowed, for one finished judgement."""

    def __init__(self, actions: CallActions, escalation: ConsiderEscalation) -> None:
        self._actions = actions
        self._escalation = escalation

    async def conclude(
        self,
        call: CallSoFar,
        notes: JudgementNotes,
        assessment: EscalationProposal,
        *,
        ending_withheld: str | None = None,
    ) -> AgentJudgement:
        """Escalates, applies the requested ending unless withheld or disallowed, and reports it."""
        readings = (*notes.escalations_requested, assessment)
        decision = await self._escalation.consider(
            call, most_pressing(readings, circumstances_of(call))
        )
        ending = notes.requested_ending
        if ending is not None:
            refused_because = ending_withheld or _why_not_to_end(ending, decision)
            if refused_because is None:
                await self._actions.end_call(call.call_id, ending, assessment)
                notes.call_ended()
            else:
                notes.refused(ToolRefusal(END_CALL, refused_because))
        return AgentJudgement(
            proposal=assessment,
            escalation=decision,
            refusals=notes.refusals,
            ended=notes.ended,
        )


def _why_not_to_end(ending: CallEnding, decision: EscalationDecision) -> str | None:
    if ending is CallEnding.HANDED_OVER:
        if decision.is_immediate:
            return None
        return "the user was not reached for this call, so it was not handed over"
    if decision.is_immediate:
        return "the user's rules call for reaching the user, so the call was not ended"
    return None
