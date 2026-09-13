"""What the user reads afterwards.

Structured, and short. A summary that restates the conversation is a transcript with extra
steps, and the reason this type exists is that nobody reads those.

It outlives the transcript by design: the transcript is deleted on a schedule, and the summary
has to still make sense on its own after that. That is why every fact the user might need is a
field here rather than a reference into the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.intent import CallImportance, CallIntent


class CallOutcome(StrEnum):
    """How it ended, in the terms a person would use.

    Distinct from `CallState`: the state machine's endings are about the mechanism, and these
    are about what happened. A call that reached COMPLETED could have been resolved, handed
    over, or abandoned, and a user reading their history wants to know which.
    """

    RESOLVED_BY_AGENT = "resolved_by_agent"
    HANDED_TO_USER = "handed_to_user"
    PASSED_THROUGH = "passed_through"
    REJECTED_BY_RULE = "rejected_by_rule"
    CALLER_HUNG_UP = "caller_hung_up"
    UNANSWERED_ESCALATION = "unanswered_escalation"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExtractedDetail:
    """A fact worth keeping, with the words it came from.

    `evidence` is what makes an extraction checkable. A reference number with no source is
    something the user has to verify themselves, which is most of the value gone; and an
    extraction that cannot be traced is one nobody can tell apart from an invention.
    """

    label: str
    value: str
    evidence: str | None = None

    def __post_init__(self) -> None:
        if not self.label.strip() or not self.value.strip():
            raise InvariantError("an extracted detail needs both a label and a value")


# Longer than this and it is not a summary. The number is enforced rather than suggested,
# because "keep it short" in a prompt is a preference and a limit here is a guarantee.
MAX_HEADLINE_CHARACTERS = 280


@dataclass(frozen=True, slots=True)
class CallSummary:
    """The durable record of one call."""

    call_id: CallId
    caller: Caller
    intent: CallIntent
    importance: CallImportance
    outcome: CallOutcome
    # Out of the repr: they quote the call, and a repr is what a log line or an assertion prints.
    headline: str = field(repr=False)
    started_at: datetime
    ended_at: datetime
    human_joined_at: datetime | None = None
    escalation_reason: EscalationReason | None = None
    details: tuple[ExtractedDetail, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        if not self.headline.strip():
            raise InvariantError("a summary with no headline tells the user nothing")
        if len(self.headline) > MAX_HEADLINE_CHARACTERS:
            raise InvariantError(
                f"a headline is at most {MAX_HEADLINE_CHARACTERS} characters; beyond that it "
                f"is a transcript with extra steps, and nobody reads those"
            )
        if self.ended_at < self.started_at:
            raise InvariantError("a call cannot end before it started")
        if self.human_joined_at is not None and not (
            self.started_at <= self.human_joined_at <= self.ended_at
        ):
            raise InvariantError("the user joined outside the call they joined")
        if self.human_joined_at is not None and self.escalation_reason is None:
            raise InvariantError(
                "a call the user joined was escalated, and the reason is what the history "
                "shows them"
            )

    @property
    def human_joined(self) -> bool:
        return self.human_joined_at is not None

    @property
    def duration_seconds(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()

    def detail(self, label: str) -> ExtractedDetail | None:
        """The first detail with this label, if it was found."""
        return next((item for item in self.details if item.label == label), None)
