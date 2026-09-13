"""Calls a handset reports about itself.

A transport that decides calls where they are — on the handset, before it rings — cannot be
asked what happened. It tells, afterwards, over the network, whenever it next can. These are the
shapes of that telling, and the storage that makes a report arriving twice count once.

The identifiers in a report are the handset's own. They are unique to that handset's account
and nothing more, so everything here is keyed by the user the report arrived from: two accounts
whose handsets happen to choose the same identifier are two different calls.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.call_transport import CallEventKind

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.identifiers import CallId, EventId, UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.call_transport import CallEvent, ScreeningDecision

# What a handset can observe about its own call without being the phone app: that it arrived,
# that somebody picked it up, and that it is over. Nobody joins or leaves a handset's call in a
# way the handset can report, so the rest of the vocabulary is refused rather than accepted.
_REPORTABLE_KINDS: Final = frozenset(
    {CallEventKind.INCOMING, CallEventKind.ANSWERED, CallEventKind.ENDED}
)


class CallEnding(StrEnum):
    """How a reported call ended."""

    # Refused before it rang, by the user's own rules.
    SCREENED_OUT = "screened_out"
    # Rang, and nobody answered.
    MISSED = "missed"
    # Answered, then hung up.
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class CallReport:
    """One thing a handset says happened to one of its calls."""

    event_id: EventId
    call_id: CallId
    kind: CallEventKind
    occurred_at: datetime
    caller_number: PhoneNumber | None = None
    screening: ScreeningDecision | None = None
    ending: CallEnding | None = None

    def __post_init__(self) -> None:
        if self.kind not in _REPORTABLE_KINDS:
            raise InvariantError(
                f"a handset cannot observe {self.kind} on its own calls, so it cannot report it"
            )
        if self.occurred_at.tzinfo is None:
            raise InvariantError("a reported moment must carry its timezone")
        if self.screening is not None and self.kind is not CallEventKind.INCOMING:
            raise InvariantError("a screening decision is reported on the incoming event only")
        if (self.ending is not None) != (self.kind is CallEventKind.ENDED):
            raise InvariantError("an ended call says how it ended, and nothing else does")


class CallReportRepository(ABC):
    """Every report a handset has made, by the user it was made for."""

    @abstractmethod
    async def record(self, user_id: UserId, report: CallReport) -> bool:
        """Store the report. False when this user's handset already reported this event.

        A handset resends whatever it has not seen acknowledged, so a repeat is ordinary. It is
        recognised by the handset's own event identifier, within this user only.
        """

    @abstractmethod
    async def has_ended(self, user_id: UserId, call_id: CallId) -> bool:
        """Whether this user's handset has already reported the call as over."""


class CallEventSink(ABC):
    """Where a reported call becomes an event the rest of the product sees."""

    @abstractmethod
    async def publish(self, user_id: UserId, event: CallEvent) -> None:
        """Hand on an event the user's handset reported. Returns promptly: a report is
        acknowledged, not processed, here.

        The user is named so that what one account's handset sends is bounded on its own, and a
        handset reporting in a loop cannot crowd out every other account's calls.
        """
