"""The one path by which a call reaches the user.

The judgement's conclusion uses it, once the model has finished, on the most pressing reading the
model gave — whether it asked for a person or only assessed a call that calls for one. One path,
because two would be two places deciding whether a phone rings, and the second one written is the
one that forgets a rule.

The policy decides; this remembers. A decision is taken afresh every time it is asked for, but a
call reaches the user at most once. A call already escalated has done everything escalation can do,
and a caller who keeps insisting is not a reason to keep ringing — so the user's phone rings once
for one call, however the model is talked into it.

Looks at the same call take turns. Deciding whether the call was already escalated, reaching the
user and writing down that they were reached happen under one lock per call, so two looks in flight
at once cannot both ring, and a failure never leaves a call marked as escalated when nobody was
reached.
"""

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
        # The calls that have reached the user.
        self._escalated: set[CallId] = set()
        self._turns: defaultdict[CallId, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def consider(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """The policy's decision on this proposal, acted on if it is one nobody has acted on yet."""
        decision = decide_escalation(proposal, circumstances_of(call))
        if not decision.required:
            return decision
        async with self._turns[call.call_id]:
            if call.call_id not in self._escalated:
                # Written only once the user was reached. A mark left behind by a failure is a call
                # that can never reach the user again.
                await self._actions.escalate(call.call_id, decision)
                self._escalated.add(call.call_id)
        return decision

    def forget(self, call_id: CallId) -> None:
        """Let go of everything kept about a call, once nothing will judge it again.

        Called by orchestration (Phase 8) when a call is over. Without it, every call this process
        ever handled is remembered for as long as the process runs.
        """
        self._escalated.discard(call_id)
        self._turns.pop(call_id, None)
