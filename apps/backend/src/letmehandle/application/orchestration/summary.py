"""The summary written as a call ends: its facts, a summary of them, and the agent's record."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.application.orchestration.metrics import DEGRADED, SUMMARY_FAILED, SUMMARY_SECONDS
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.application.resilience.timing import Stopwatch, within
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.models.call import CallHandling
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import ExtractedDetail
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from letmehandle.application.agent.ports import OutcomeRecord
    from letmehandle.application.orchestration.run import RunContext
    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.policy.escalation import EscalationProposal

logger = get_logger(__name__)

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


async def summary_of(
    call: CallSession,
    findings: Findings,
    *,
    locale: str,
    context: RunContext,
    note: Callable[[MarkKind, str], None],
) -> CallSummary:
    """The ended call's summary: written within the summary bound, with what the run found."""
    facts = facts_of(call, findings)
    written = await _written(call, facts, locale=locale, context=context, note=note)
    return with_findings(written, call, findings)


async def _written(
    call: CallSession,
    facts: CallFacts,
    *,
    locale: str,
    context: RunContext,
    note: Callable[[MarkKind, str], None],
) -> CallSummary:
    summariser = context.summariser
    if summariser is None or call.handling is not CallHandling.ASSISTANT:
        return fallback_summary(facts, locale=locale)
    if context.circuits[Dependency.MODEL].is_refusing:
        context.metrics.increment(DEGRADED, {"stage": "summary"})
        note(MarkKind.DEGRADED, "summary")
        return fallback_summary(facts, locale=locale)
    stopwatch = Stopwatch()
    try:
        with context.tracer.span("summary.write", dependency=Dependency.MODEL.value):
            written = await within(
                context.bounds.summary,
                lambda: summariser.summarise(facts, call.transcript, locale=locale),
            )
    except TimeoutError:
        logger.warning("call.summary_failed", error="TimeoutError")
        context.metrics.increment(SUMMARY_FAILED, {"kind": FailureKind.TIMEOUT})
        note(MarkKind.FAILURE, f"summary.{FailureKind.TIMEOUT}")
        context.metrics.observe(SUMMARY_SECONDS, stopwatch.seconds, {"outcome": "fallback"})
        return fallback_summary(facts, locale=locale)
    context.metrics.observe(SUMMARY_SECONDS, stopwatch.seconds, {"outcome": "written"})
    return written


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
    # The agent's record is laid over only a completed call whose outcome it matches.
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
