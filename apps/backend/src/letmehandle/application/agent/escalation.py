"""The one path by which a call reaches the user.

The escalation tool uses it when the model asks for a person, and the agent's end-of-turn check
uses it when the model did not ask but the call calls for one. One path, because two would be two
places deciding whether a phone rings, and the second one written is the one that forgets a rule.

The policy decides; this remembers. A decision is taken afresh every time it is asked for, but a
call reaches the user at most once for each level of urgency, and only ever upwards:

- A call not yet escalated is escalated when the policy says so.
- A call escalated "while convenient" — typically inside quiet hours — may be escalated once more,
  immediately, when the policy later decides it is urgent. The user who would have been woken for
  that call on its first turn should not sleep through it because it became urgent on its third,
  and the upgrade gives a caller nothing they could not have had by being convincing sooner.
- Nothing else rings again. A call already escalated immediately has done everything escalation
  can do, and a caller who keeps insisting is not a reason to keep ringing.

So the user's phone rings at most twice for one call, however the model is talked into it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
        now=call.now,
        from_important_contact=call.from_important_contact,
    )


class EscalationService:
    """Decides on a proposal and, when the decision is to escalate, reaches the user once."""

    def __init__(self, actions: CallActions) -> None:
        self._actions = actions
        # Per call, whether the escalation already made was immediate. Absent means none was made.
        self._escalated_immediately: dict[CallId, bool] = {}

    def has_escalated(self, call_id: CallId) -> bool:
        """Whether the user has been asked to join this call."""
        return call_id in self._escalated_immediately

    async def consider(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """The policy's decision on this proposal, acted on if it is one nobody has acted on yet."""
        decision = decide_escalation(proposal, circumstances_of(call))
        if not decision.required or not self._is_new(call.call_id, decision):
            return decision

        # Marked before the await, so a second look at the same call arriving while the first is
        # still reaching the user finds it already escalated rather than ringing twice.
        previous = self._escalated_immediately.get(call.call_id)
        self._escalated_immediately[call.call_id] = decision.is_immediate
        try:
            await self._actions.escalate(call.call_id, decision)
        except BaseException:
            # The user was not reached, so the call must not read as escalated: a mark left behind
            # by a failure is a call that can never reach the user again.
            self._forget(call.call_id, previous)
            raise
        return decision

    def _is_new(self, call_id: CallId, decision: EscalationDecision) -> bool:
        previous = self._escalated_immediately.get(call_id)
        return previous is None or (not previous and decision.is_immediate)

    def _forget(self, call_id: CallId, previous: bool | None) -> None:
        if previous is None:
            del self._escalated_immediately[call_id]
        else:
            self._escalated_immediately[call_id] = previous
