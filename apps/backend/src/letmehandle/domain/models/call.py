"""A call, for its whole life.

The aggregate. It owns its own state and refuses to be moved illegally, so that no caller can
put a call somewhere the state machine forbids by assigning to a field.

What it deliberately does not own: idempotency, timeouts, concurrency, and the decision of
when to move. Those belong to the orchestrator in phase 8. This type knows the rules of the
game, not the strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call_state import CallState, is_terminal, move

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.identifiers import CallId, UserId


class Speaker(StrEnum):
    """Who said something."""

    CALLER = "caller"
    AGENT = "agent"
    HUMAN = "human"


class ParticipantRole(StrEnum):
    """What someone is doing on the call.

    `HUMAN` is the user themselves, joined after an escalation. They are a participant like
    any other, which is what makes a three-way call expressible without a special case.
    """

    CALLER = "caller"
    AGENT = "agent"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class Participant:
    """Somebody, or something, on the call."""

    role: ParticipantRole
    joined_at: datetime
    left_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.left_at is not None and self.left_at < self.joined_at:
            raise InvariantError("a participant cannot leave before they joined")

    @property
    def is_present(self) -> bool:
        return self.left_at is None

    def departing(self, at_instant: datetime) -> Participant:
        return Participant(self.role, self.joined_at, at_instant)


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One thing somebody said.

    Held in memory for the length of the call. Whether it is written down, and for how long, is
    a decision made outside the domain — see the retention decision in the architecture record.
    """

    speaker: Speaker
    text: str
    at_instant: datetime

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantError("a transcript entry with nothing in it is not an utterance")


@dataclass(slots=True)
class CallSession:
    """One call.

    Mutable, unlike everything else here, because a call genuinely changes: that is what it is.
    The mutation is confined to the methods below, which is what keeps the state machine
    authoritative.
    """

    id: CallId
    user_id: UserId
    caller: Caller
    started_at: datetime
    _state: CallState = field(default=CallState.RECEIVED, init=False)
    _participants: list[Participant] = field(default_factory=list, init=False)
    _transcript: list[TranscriptEntry] = field(default_factory=list, init=False)
    ended_at: datetime | None = field(default=None, init=False)

    @property
    def state(self) -> CallState:
        """Read-only. Changing it goes through `move_to`, which applies the rules."""
        return self._state

    @property
    def is_over(self) -> bool:
        return is_terminal(self._state)

    @property
    def participants(self) -> tuple[Participant, ...]:
        return tuple(self._participants)

    @property
    def present_participants(self) -> tuple[Participant, ...]:
        return tuple(participant for participant in self._participants if participant.is_present)

    @property
    def transcript(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._transcript)

    def move_to(self, state: CallState, *, at_instant: datetime | None = None) -> None:
        """Move the call, or raise naming both states.

        `at_instant` is required when moving to an ending, because a call's duration is the
        difference between two recorded moments and a missing one makes every later summary
        and metric wrong.
        """
        self._state = move(self._state, state)
        if is_terminal(self._state):
            if at_instant is None:
                raise InvariantError(
                    "a call that has ended must record when; its duration is read from it"
                )
            self.ended_at = at_instant

    def add_participant(self, role: ParticipantRole, at_instant: datetime) -> None:
        """Put somebody on the call.

        A role can only be present once. A second agent or a second human would make
        "who is on this call" ambiguous, and every later question about the call with it.
        """
        if self.is_over:
            raise InvariantError("nobody can join a call that has ended")
        if any(p.role is role and p.is_present for p in self._participants):
            raise InvariantError(f"the {role} is already on this call")
        self._participants.append(Participant(role, at_instant))

    def remove_participant(self, role: ParticipantRole, at_instant: datetime) -> None:
        """Take somebody off it.

        Removing somebody who is not there is an error rather than a no-op: it means a caller
        believes the call has a shape it does not, and silence would hide that.
        """
        for index, participant in enumerate(self._participants):
            if participant.role is role and participant.is_present:
                self._participants[index] = participant.departing(at_instant)
                return
        raise InvariantError(f"the {role} is not on this call")

    def has_participant(self, role: ParticipantRole) -> bool:
        return any(p.role is role and p.is_present for p in self._participants)

    def record(self, speaker: Speaker, text: str, at_instant: datetime) -> None:
        """Add to the transcript."""
        if self.is_over:
            raise InvariantError("nothing more is said on a call that has ended")
        self._transcript.append(TranscriptEntry(speaker, text, at_instant))

    def duration_seconds(self) -> float | None:
        """How long the call lasted, or None while it is still going."""
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()
