"""The one path by which a call reaches the user: at most once per urgency, only upwards."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING

from letmehandle.application.agent.ports import ConsiderEscalation
from letmehandle.domain.policy.escalation import CallCircumstances, decide_escalation

if TYPE_CHECKING:
    from letmehandle.application.agent.ports import CallActions, CallSoFar
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.policy.escalation import EscalationProposal


def circumstances_of(call: CallSoFar) -> CallCircumstances:
    """What the policy may consider: the user's rules and grant, never what was said."""
    return CallCircumstances(
        rules=call.rules,
        authority=call.authority,
        from_important_contact=call.from_important_contact,
    )


class EscalationService(ConsiderEscalation):
    """Decides on a proposal and, when the decision is to escalate, reaches the user once."""

    def __init__(self, actions: CallActions) -> None:
        self._actions = actions
        # Per call, whether the escalation made was immediate; absent means none was made.
        self._escalated_immediately: dict[CallId, bool] = {}
        self._turns: defaultdict[CallId, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def consider(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """The policy's decision on this proposal, acted on if it is one nobody has acted on yet."""
        decision = decide_escalation(proposal, circumstances_of(call))
        if not decision.required:
            return decision
        async with self._turns[call.call_id]:
            if self._is_new(call.call_id, decision):
                # Marked only once reached, so a failure leaves the call able to escalate.
                await self._actions.escalate(call.call_id, decision)
                self._escalated_immediately[call.call_id] = decision.is_immediate
        return decision

    def forget(self, call_id: CallId) -> None:
        """Drops everything kept about a call once nothing will judge it again."""
        self._escalated_immediately.pop(call_id, None)
        self._turns.pop(call_id, None)

    def _is_new(self, call_id: CallId, decision: EscalationDecision) -> bool:
        previous = self._escalated_immediately.get(call_id)
        return previous is None or (not previous and decision.is_immediate)
