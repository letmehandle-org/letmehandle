"""What the user is told about an escalation, kept so it can be read again.

The push notification is an accelerator and never the only path (D-016): an app opened without
one fetches this instead. So the context is stored when it is first dispatched, and what the
notification carries is derived from it rather than the other way round.

It holds the facts a person needs to walk into the call and nothing else — why, who as far as
anyone knows, what has been established, what is needed. No transcript, no recording reference,
no number: a label for the caller, never the caller's details.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.identifiers import CallId

# Bounds on what is stored. The notification trims to fit a platform; these exist so that a
# runaway summary is refused where it is produced rather than stored and shown to somebody.
MAX_CALL_ID_LENGTH: Final = 64
MAX_CALLER_LABEL_LENGTH: Final = 120
MAX_DETAIL_LENGTH: Final = 1000


class EscalationStatus(StrEnum):
    """Whether the call the escalation belongs to is still going.

    A notification can arrive after the call has ended, and that is a designed state: the app
    shows what happened instead of a live context for a call nobody can join.
    """

    LIVE = "live"
    ENDED = "ended"


class NotificationDelivery(StrEnum):
    """What became of telling the user, surfaced in the app rather than raised anywhere."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    NO_DEVICES = "no_devices"


@dataclass(frozen=True, slots=True)
class EscalationContext:
    """One escalation, as the user is shown it."""

    call_id: CallId
    reason: EscalationReason
    raised_at: datetime
    caller_label: str | None = None
    established: str | None = None
    needed: str | None = None
    status: EscalationStatus = EscalationStatus.LIVE
    ended_at: datetime | None = None
    delivery: NotificationDelivery = NotificationDelivery.PENDING

    def __post_init__(self) -> None:
        if self.raised_at.tzinfo is None:
            raise InvariantError("an escalation's time must carry its zone")
        if len(self.call_id.value) > MAX_CALL_ID_LENGTH:
            raise InvariantError(
                f"a call id longer than {MAX_CALL_ID_LENGTH} characters cannot bind a "
                "notification to its call"
            )
        _check_text("caller label", self.caller_label, MAX_CALLER_LABEL_LENGTH)
        _check_text("what has been established", self.established, MAX_DETAIL_LENGTH)
        _check_text("what is needed", self.needed, MAX_DETAIL_LENGTH)
        if (self.status is EscalationStatus.ENDED) != (self.ended_at is not None):
            raise InvariantError("a call has an end time exactly when it has ended")
        if self.ended_at is not None and self.ended_at < self.raised_at:
            raise InvariantError("a call cannot end before its escalation was raised")

    @property
    def is_live(self) -> bool:
        return self.status is EscalationStatus.LIVE

    def ended(self, at_instant: datetime) -> EscalationContext:
        """The same escalation, for a call that is over. Ending twice keeps the first end."""
        if not self.is_live:
            return self
        ended_at = max(at_instant, self.raised_at)
        return replace(self, status=EscalationStatus.ENDED, ended_at=ended_at)


def _check_text(what: str, value: str | None, limit: int) -> None:
    if value is None:
        return
    if not value.strip():
        raise InvariantError(f"the {what} is either absent or says something")
    if len(value) > limit:
        raise InvariantError(f"the {what} is longer than {limit} characters")
