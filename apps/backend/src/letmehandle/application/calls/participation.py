"""When somebody first came on a call, read from its participants.

In one place because the summary written as a call ends and the history read long after both ask
it, and two readings of the same participants that disagreed would show the user joining a call
at one moment in its summary and another in its timeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.call import CallSession, ParticipantRole


def first_joined(call: CallSession, *roles: ParticipantRole) -> datetime | None:
    """The earliest moment anybody in `roles` joined, or None if none of them ever did."""
    return min(
        (participant.joined_at for participant in call.participants if participant.role in roles),
        default=None,
    )
