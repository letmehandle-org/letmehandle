"""The summary written as a call ends: what the run learned, laid over the summary it was given.

The facts come first, because they are the one reading of the call that needs nobody — who joined
and when, why the user was asked for, what the agent last judged the call to be. The summary under
them is the summariser's for a call the assistant took, and the facts' own otherwise; the agent's
account of the outcome, when it wrote one that holds and names the ending the facts establish,
goes over either. A record the domain refuses, such as a headline too long to be one, is left out
rather than leaving the call without a summary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import CallFacts
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import ExtractedDetail

if TYPE_CHECKING:
    from letmehandle.application.agent.ports import OutcomeRecord
    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.policy.escalation import EscalationProposal

# The label a message the caller left is kept under among a summary's details.
MESSAGE_LABEL: Final = "message"


@dataclass(frozen=True, slots=True)
class Findings:
    """What a call's run learned that its stored record does not hold."""

    outcome: OutcomeRecord | None = None
    proposal: EscalationProposal | None = None
    escalation_reason: EscalationReason | None = None
    caller_hung_up: bool = False
    messages: tuple[str, ...] = ()


def facts_of(call: CallSession, findings: Findings) -> CallFacts:
    """What is known for certain about an ended call, with the agent's last reading of it."""
    proposal = findings.proposal
    return CallFacts(
        call=call,
        escalation_reason=findings.escalation_reason,
        caller_hung_up=findings.caller_hung_up,
        intent=CallIntent.UNDETERMINED if proposal is None else proposal.intent,
        importance=CallImportance.ROUTINE if proposal is None else proposal.importance,
    )


def with_findings(summary: CallSummary, call: CallSession, findings: Findings) -> CallSummary:
    """`summary`, with the messages taken and the agent's own record. Never raises."""
    messages = tuple(ExtractedDetail(MESSAGE_LABEL, message) for message in findings.messages)
    kept = replace(summary, details=(*summary.details, *messages))
    record = findings.outcome
    # Only for a call that ran its course. A rejected or failed call is what its state says it was,
    # whatever the agent wrote down before that happened. And only for the ending the call had: the
    # agent writes its record mid-call, and one written as the user's phone rang can claim a
    # handover the caller hung up before — the summariser is held to the facts' outcome, and so is
    # this.
    if (
        record is None
        or call.state is not CallState.COMPLETED
        or record.outcome is not summary.outcome
    ):
        return kept
    try:
        return replace(
            summary,
            outcome=record.outcome,
            headline=record.headline,
            details=(*record.details, *messages),
        )
    except InvariantError:
        return kept
