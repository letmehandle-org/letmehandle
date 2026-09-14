"""When somebody first came on a call, read from its participants."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.models.call import ParticipantRole

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.call import CallSession


def first_joined(call: CallSession, *roles: ParticipantRole) -> datetime | None:
    """The earliest moment anybody in `roles` joined, or None if none of them ever did."""
    return min(
        (participant.joined_at for participant in call.participants if participant.role in roles),
        default=None,
    )


def answered_at(call: CallSession) -> datetime | None:
    """When the call was first picked up, by the assistant or by the user, if it ever was."""
    return first_joined(call, ParticipantRole.AGENT, ParticipantRole.HUMAN)


def human_joined_at(call: CallSession) -> datetime | None:
    """When the user first joined a call the assistant was on, if they did."""
    if first_joined(call, ParticipantRole.AGENT) is None:
        return None
    return first_joined(call, ParticipantRole.HUMAN)
