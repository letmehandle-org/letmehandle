"""Whether the user is needed on a call: the model proposes, and these fixed rules decide."""

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
    """What the model believes about the call so far, including what the caller asked it to do."""

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
    """Whether to reach the user, why, and how urgently; pure."""
    # A suspected scam never reaches the user unless it is from a contact marked important.
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
    """Of several readings of a call, the one the rules act on most strongly, the last of equals."""
    if not proposals:
        raise InvariantError("there is no most pressing reading of no readings")
    return max(
        reversed(proposals),
        key=lambda proposal: _pressure(decide_escalation(proposal, circumstances)),
    )


def _pressure(decision: EscalationDecision) -> int:
    if not decision.required:
        return 0
    return 2 if decision.is_immediate else 1


def _reason(
    proposal: EscalationProposal, circumstances: CallCircumstances
) -> EscalationReason | None:
    """The first reason that applies, in a fixed order that ends with the user's own threshold."""
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
