"""The summary written as a call ends: the agent's, when it wrote one that holds, else the facts'.

The fallback is built first either way, because it is the one reading of the call's facts — who
joined and when, why the user was asked for — and the agent's summary is those facts with the
agent's account of the outcome laid over them. A record the domain refuses, such as a headline too
long to be one, falls back rather than leaving the call without a summary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import CallFacts, fallback_summary
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


def summary_of(call: CallSession, findings: Findings, *, locale: str) -> CallSummary:
    """The summary of an ended call. Never raises for one that has ended."""
    proposal = findings.proposal
    facts = fallback_summary(
        CallFacts(
            call=call,
            escalation_reason=findings.escalation_reason,
            caller_hung_up=findings.caller_hung_up,
            intent=CallIntent.UNDETERMINED if proposal is None else proposal.intent,
            importance=CallImportance.ROUTINE if proposal is None else proposal.importance,
        ),
        locale=locale,
    )
    messages = tuple(ExtractedDetail(MESSAGE_LABEL, message) for message in findings.messages)
    record = findings.outcome
    # Only for a call that ran its course. A rejected or failed call is what its state says it was,
    # whatever the agent wrote down before that happened.
    if record is None or call.state is not CallState.COMPLETED:
        return replace(facts, details=messages)
    try:
        return replace(
            facts,
            outcome=record.outcome,
            headline=record.headline,
            details=(*record.details, *messages),
        )
    except InvariantError:
        return replace(facts, details=messages)
