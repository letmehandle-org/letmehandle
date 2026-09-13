"""What the caller wants, and how much it matters, as two independent judgements."""

from __future__ import annotations

from enum import IntEnum, StrEnum


class CallIntent(StrEnum):
    """What the call is for; `UNDETERMINED` is the answer while there is none yet."""

    UNDETERMINED = "undetermined"
    DELIVERY_IN_PROGRESS = "delivery_in_progress"
    APPOINTMENT = "appointment"
    ENQUIRY = "enquiry"
    PERSONAL = "personal"
    SERVICE_ISSUE = "service_issue"
    SALES = "sales"
    SUSPECTED_FRAUD = "suspected_fraud"


class CallImportance(IntEnum):
    """How much a call matters to the user, ordered, with gaps so a level can be inserted."""

    IGNORABLE = 10
    LOW = 20
    ROUTINE = 30
    NOTABLE = 40
    URGENT = 50
