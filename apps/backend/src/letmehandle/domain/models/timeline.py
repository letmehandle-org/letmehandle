"""A call's life as structure: what it moved through, and what failed on the way, with when.

Kept so that a call that went wrong can be understood afterwards from its identifier alone, without
reading what anybody said on it. So a mark is a kind and a name from a closed vocabulary — a state,
or a stage and the kind of failure there — and a moment. Nothing in a mark comes from the caller,
the user or the conversation, and the shape of a name is checked so that nothing can.
"""

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
            # The name is left out: a mark that could carry content must not repeat it anywhere.
            raise InvariantError("a timeline mark is named in lower-case words, never in content")


@dataclass(frozen=True, slots=True)
class EscalationOutline:
    """Why the user was asked for, where that escalation stands, and what became of its push."""

    reason: EscalationReason
    status: EscalationStatus
    delivery: NotificationDelivery


@dataclass(frozen=True, slots=True)
class CallOutline:
    """What is stored about a call that is structure rather than content, and its marks in order.

    Deliberately without the caller, the owner, the transcript and the summary's words: what is
    here is enough to see what happened to a call, and nothing here says who was on it or what
    they said.
    """

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
