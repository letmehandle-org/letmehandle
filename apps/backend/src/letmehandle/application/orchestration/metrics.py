"""The metrics a call's run records, declared once for every part of the run that records them."""

from __future__ import annotations

from typing import Final

from letmehandle.application.orchestration.routing import Route
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.models.call_state import TERMINAL
from letmehandle.domain.ports.call_transport import ParticipantOutcome
from letmehandle.observability import catalogue

# What a run asks of the transport, each its own stage of the call.
TELEPHONY_STAGES: Final = frozenset({"answer", "dial", "cancel", "terminate"})

PROVIDER_FAILED: Final = catalogue.count(
    "call.provider_failed", stage={"owner", "speech", *TELEPHONY_STAGES}, kind=FailureKind
)
JUDGEMENT_FAILED: Final = catalogue.count("call.judgement_failed", kind=FailureKind)
SUMMARY_FAILED: Final = catalogue.count("call.summary_failed", kind=FailureKind)
CALL_ENDED: Final = catalogue.count("call.ended", outcome=TERMINAL)
# A call ended by a bound on calls themselves rather than by anything that happened on it.
CALL_BOUNDED: Final = catalogue.count("call.bounded", kind={"duration", "live_calls"})
ROUTED: Final = catalogue.count("call.routed", outcome={*Route, "nobody"})
# How an escalation that rang the user turned out, or that it never rang, or the call ended first.
ESCALATION_RESOLVED: Final = catalogue.count(
    "call.escalation_resolved", outcome={*ParticipantOutcome, "dial_refused", "call_ended"}
)
# A transport event already acted on, or about a call already gone, delivered again.
DUPLICATE_IGNORED: Final = catalogue.count("call.duplicate_ignored", stage={"repeated", "late"})
# A call handled without a dependency whose circuit was open.
DEGRADED: Final = catalogue.count("call.degraded", stage={"speech", "summary"})

PROVIDER_SECONDS: Final = catalogue.measure("call.provider_seconds", stage=TELEPHONY_STAGES)
SPEECH_OPEN_SECONDS: Final = catalogue.measure(
    "call.speech_open_seconds", outcome={"opened", "failed"}
)
JUDGEMENT_SECONDS: Final = catalogue.measure("call.judgement_seconds", outcome={"judged", "failed"})
SUMMARY_SECONDS: Final = catalogue.measure("call.summary_seconds", outcome={"written", "fallback"})
