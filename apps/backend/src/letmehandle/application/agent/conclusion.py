"""Acting on a judgement, once, after the model has finished.

While a model works a call out it only asks: for the user, for the call to end. What it asks for is
written in the judgement's notes, and this is where it happens — once, outside the bound on the
model's time, and in an order that keeps the user's rules in charge of a hang-up:

1. **The escalation.** Every reading the model gave — each time it asked for the user, and its
   final assessment — is decided by the policy, and the most pressing is acted on through the
   escalation service. A caller who said somebody collapsed and then calmed down is still that
   caller, and a model that asked for the user and then forgot is still a model that asked.
2. **The ending, if it is still allowed.** A resolved or declined ending is refused when the rules
   require reaching the user now: a hang-up never cancels that. A deferred escalation — a note for
   later — holds nobody on the line. A handed-over ending is accepted
   only when step 1 reached the user immediately. If the escalation failed, nothing is ended,
   because the failure is raised before the ending is considered.

A refused ending is recorded as a refusal of `end_call`, like any other, so the user can see what
their assistant tried to do.
"""

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

# Why an ending the model asked for was not applied, when the reason is the judgement rather than
# the ending: the adapter says which, because only the adapter knows how the model's turn went.
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
        """Act on what the model asked for and concluded, and report what was done.

        `ending_withheld`, when given, refuses any ending with that reason whatever the rules say,
        for a judgement that did not finish well enough to hang up on. Raises whatever reaching the
        user raised, before any ending is applied.
        """
        readings = (*notes.escalations_requested, assessment)
        decision = await self._escalation.consider(
            call, most_pressing(readings, circumstances_of(call))
        )
        ending = notes.requested_ending
        if ending is not None:
            refused_because = ending_withheld or _why_not_to_end(ending, decision)
            if refused_because is None:
                await self._actions.end_call(call.call_id, ending)
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
    # Only an immediate escalation holds the caller on the line. One the rules defer — a note for
    # the user once quiet hours are over — needs nobody to stay, so the call may end.
    if decision.is_immediate:
        return "the user's rules call for reaching the user, so the call was not ended"
    return None
