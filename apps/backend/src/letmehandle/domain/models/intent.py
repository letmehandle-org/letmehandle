"""What the caller wants, and how much it matters.

Two separate judgements, because they vary independently: a sales call is unimportant whatever
it is about, and a delivery is important only when the courier is at the door right now.
Collapsing them into a single score would make the second impossible to express.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class CallIntent(StrEnum):
    """What the call is for.

    `UNDETERMINED` is a real answer: early in a call, or in a call that never made sense, the
    honest classification is that there is not one yet. A model forced to choose will choose
    something, and downstream rules will act on it.
    """

    UNDETERMINED = "undetermined"
    DELIVERY_IN_PROGRESS = "delivery_in_progress"
    APPOINTMENT = "appointment"
    ENQUIRY = "enquiry"
    PERSONAL = "personal"
    SERVICE_ISSUE = "service_issue"
    SALES = "sales"
    SUSPECTED_FRAUD = "suspected_fraud"


class CallImportance(IntEnum):
    """How much this matters to the user, ordered.

    An `IntEnum` because these are compared — a rule says "escalate at or above this" — and an
    ordering written as a lookup table beside an unordered enum is an ordering that drifts
    from it.

    The numbers are spaced so that a level can be inserted later without renumbering the ones
    either side, which would silently change the meaning of every stored value.
    """

    IGNORABLE = 10
    LOW = 20
    ROUTINE = 30
    NOTABLE = 40
    URGENT = 50

    @property
    def is_at_least_notable(self) -> bool:
        """Whether this is the kind of thing a person would want to know about."""
        return self >= CallImportance.NOTABLE
