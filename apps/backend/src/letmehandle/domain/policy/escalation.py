"""Whether a person is needed on a call, decided without a model.

The model proposes; this decides. A model reads the conversation and says what it thinks is going
on — how much the call matters, whether the caller asked for the user, whether something needs
the user's say. It does not decide whether the user's phone rings. That is a rule the user set,
applied the same way every time, so it can be read, tested and changed without touching a prompt,
and so a caller who talks the model into believing their call is urgent still meets the rule.

The decision runs in three steps, each written once:

1. **Never for a suspected scam from a stranger.** Putting a suspected fraudster through to the
   person they are trying to reach is the one outcome an assistant screening calls exists to
   prevent. The call history still says it happened. A contact the user marked as important is
   the exception: who they are comes from a number the user trusts, and "fraud" is only the model's
   reading, which a caller can provoke — a panicked relative sounds a lot like a scam. The rule
   that exists so a model cannot talk the user's phone into ringing must not become the way a
   model talks it out of ringing for the people the user said matter most.
2. **Is there a reason?** Taken in a fixed order, so a call with several reasons always reports
   the same one. A call with no reason to involve the user does not involve them, however
   important the model thinks it is — importance alone *is* a reason, the last in the order.
3. **Is it worth reaching them?** The user's own threshold decides, and a contact the user marked
   as important always clears it. Below the threshold the user is not reached at all: a note in
   the call history is how they hear about a call that did not matter enough.

Urgency is immediate. The user's hours do not defer it (D-029): outside them the assistant answers
nothing and calls ring the user, so the only call it is still on then is one that ran past the
end of its hours — and by then the user is somebody whose phone rings anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.intent import CallImportance, CallIntent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.authority import AgentAuthority, Capability
    from letmehandle.domain.models.preferences import CallRules


@dataclass(frozen=True, slots=True)
class EscalationProposal:
    """What the model believes about the call so far.

    Every field is the model's judgement, and none of them is trusted to decide anything alone.
    `requested_capability` is what the caller wants the assistant to do, if anything: whether the
    assistant may do it is the user's grant, checked here, never the model's opinion.
    """

    importance: CallImportance
    intent: CallIntent
    understood: bool = True
    caller_asked_for_the_user: bool = False
    needs_the_users_decision: bool = False
    requested_capability: Capability | None = None
    caller_summary: str | None = None

    def __post_init__(self) -> None:
        if self.caller_summary is not None and not self.caller_summary.strip():
            raise InvariantError("a caller summary is either absent or says something")


@dataclass(frozen=True, slots=True)
class CallCircumstances:
    """What is true of this call regardless of what anybody said on it."""

    rules: CallRules
    authority: AgentAuthority
    from_important_contact: bool = False


def decide_escalation(
    proposal: EscalationProposal, circumstances: CallCircumstances
) -> EscalationDecision:
    """Whether to reach the user, why, and how urgently. Pure: the same input, the same answer."""
    if proposal.intent is CallIntent.SUSPECTED_FRAUD and not circumstances.from_important_contact:
        return EscalationDecision.not_needed()

    reason = _reason(proposal, circumstances)
    if reason is None or not _worth_reaching(proposal, circumstances):
        return EscalationDecision.not_needed()

    return EscalationDecision.needed(
        reason,
        EscalationUrgency.IMMEDIATE,
        caller_summary=proposal.caller_summary,
    )


def most_pressing(
    proposals: Sequence[EscalationProposal], circumstances: CallCircumstances
) -> EscalationProposal:
    """Of several readings of the same call, the one the rules would act on most strongly.

    A model may ask for the user part way through and assess the call differently at the end, and
    either reading can be the one that matters: a caller who calmed down is still the caller who
    said somebody collapsed. So each is decided, and any escalation wins over none. Of readings the
    rules treat alike, the last is taken, because it was made knowing the most.
    """
    if not proposals:
        raise InvariantError("there is no most pressing reading of no readings")
    return max(
        reversed(proposals),
        key=lambda proposal: _pressure(decide_escalation(proposal, circumstances)),
    )


def _pressure(decision: EscalationDecision) -> int:
    return 1 if decision.required else 0


def _reason(
    proposal: EscalationProposal, circumstances: CallCircumstances
) -> EscalationReason | None:
    """The first reason that applies, in an order that never changes.

    Not understanding comes first because every other judgement rests on having understood. A
    caller asking for the user comes before an action the assistant may not take, because what
    they asked for is who they wanted. Importance comes last, because it is a reason only when
    nothing more specific is — and it is measured against the user's threshold, not a fixed
    level, so the rule the user set is the one a call is held to.
    """
    requested = proposal.requested_capability
    authority = circumstances.authority
    candidates = (
        (not proposal.understood, EscalationReason.CANNOT_UNDERSTAND_THE_CALLER),
        (proposal.caller_asked_for_the_user, EscalationReason.CALLER_ASKED_FOR_THE_USER),
        (
            requested is not None and not authority.allows(requested),
            EscalationReason.ACTION_NOT_AUTHORISED,
        ),
        (proposal.needs_the_users_decision, EscalationReason.DECISION_NEEDS_THE_USER),
        (
            proposal.importance >= circumstances.rules.escalate_at_or_above,
            EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT,
        ),
    )
    return next((reason for applies, reason in candidates if applies), None)


def _worth_reaching(proposal: EscalationProposal, circumstances: CallCircumstances) -> bool:
    return (
        circumstances.from_important_contact
        or proposal.importance >= circumstances.rules.escalate_at_or_above
    )
