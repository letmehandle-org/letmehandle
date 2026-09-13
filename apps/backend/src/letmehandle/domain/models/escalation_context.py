"""What the user is told about an escalation, stored for the app to read without a push (D-016)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.identifiers import CallId

# Bounds on what is stored, refusing a runaway summary where it is produced.
MAX_CALL_ID_LENGTH: Final = 64
MAX_CALLER_LABEL_LENGTH: Final = 120
MAX_DETAIL_LENGTH: Final = 1000


class EscalationStatus(StrEnum):
    """Whether the call the escalation belongs to is still going."""

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
    """One escalation as the user is shown it: a label for the caller, never their details."""

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
        return replace(self, status=EscalationStatus.ENDED, ended_at=at_instant)


def _check_text(what: str, value: str | None, limit: int) -> None:
    if value is None:
        return
    if not value.strip():
        raise InvariantError(f"the {what} is either absent or says something")
    if len(value) > limit:
        raise InvariantError(f"the {what} is longer than {limit} characters")
