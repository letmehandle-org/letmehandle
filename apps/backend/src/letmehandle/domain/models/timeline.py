"""A call's life as structure only: the states it entered and what failed on the way."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.call import CallHandling, Participant
    from letmehandle.domain.models.call_state import CallState
    from letmehandle.domain.models.escalation import EscalationReason
    from letmehandle.domain.models.escalation_context import (
        EscalationStatus,
        NotificationDelivery,
    )
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.summary import CallOutcome

MAX_MARK_NAME_LENGTH: Final = 64

# Lower-case words joined by dots or underscores: `agent_handling`, `dial.timeout`.
_NAME: Final = re.compile(r"[a-z][a-z_]*(\.[a-z][a-z_]*)*")


class MarkKind(StrEnum):
    """What a mark records."""

    # The call entered the state the mark names.
    TRANSITION = "transition"
    # A stage of the call failed, named as the stage and the kind of failure.
    FAILURE = "failure"
    # The call was handled without something it depends on, named by what it went without.
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class TimelineMark:
    """One moment in a call's life."""

    at: datetime
    kind: MarkKind
    name: str

    def __post_init__(self) -> None:
        if len(self.name) > MAX_MARK_NAME_LENGTH or not _NAME.fullmatch(self.name):
            # The refused name is left out of the message, since it may be content.
            raise InvariantError("a timeline mark is named in lower-case words, never in content")


@dataclass(frozen=True, slots=True)
class EscalationOutline:
    """Why the user was asked for, where that escalation stands, and what became of its push."""

    reason: EscalationReason
    status: EscalationStatus
    delivery: NotificationDelivery


@dataclass(frozen=True, slots=True)
class CallOutline:
    """A call's structure and marks in order, with nothing naming who was on it or what was said."""

    call_id: CallId
    state: CallState
    handling: CallHandling | None
    started_at: datetime
    ended_at: datetime | None
    escalated_at: datetime | None
    participants: tuple[Participant, ...]
    escalation: EscalationOutline | None
    outcome: CallOutcome | None
    marks: tuple[TimelineMark, ...]
