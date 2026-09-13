"""A call for its whole life: the aggregate that refuses any move the state machine forbids."""

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


class CallHandling(StrEnum):
    """Whom routing gave the call to, read from the moves the call made."""

    PASSED_THROUGH = "passed_through"
    ASSISTANT = "assistant"


_HANDLING_BY_STATE: dict[CallState, CallHandling] = {
    CallState.PASSTHROUGH: CallHandling.PASSED_THROUGH,
    CallState.AGENT_HANDLING: CallHandling.ASSISTANT,
}


class ParticipantRole(StrEnum):
    """What someone is doing on the call; `HUMAN` is the user, joined after an escalation."""

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
        """The participant having left, clamped to when they joined against clock skew."""
        return Participant(self.role, self.joined_at, max(at_instant, self.joined_at))


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One thing somebody said, held in memory for the length of the call."""

    speaker: Speaker
    # Out of the repr, which a log line or a failing assertion prints.
    text: str = field(repr=False)
    at_instant: datetime

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantError("a transcript entry with nothing in it is not an utterance")


@dataclass(slots=True)
class CallSession:
    """One call, mutable only through the methods that apply the state machine."""

    id: CallId
    user_id: UserId
    caller: Caller
    started_at: datetime
    _state: CallState = field(default=CallState.RECEIVED, init=False)
    _participants: list[Participant] = field(default_factory=list, init=False)
    _transcript: list[TranscriptEntry] = field(default_factory=list, init=False)
    ended_at: datetime | None = field(default=None, init=False)
    handling: CallHandling | None = field(default=None, init=False)
    # When the assistant first asked for the user, whether or not the user was reached.
    escalated_at: datetime | None = field(default=None, init=False)

    @classmethod
    def restore(
        cls,
        *,
        id: CallId,  # noqa: A002 - the field is called id, and the argument names the field
        user_id: UserId,
        caller: Caller,
        started_at: datetime,
        state: CallState,
        participants: tuple[Participant, ...],
        ended_at: datetime | None,
        handling: CallHandling | None = None,
        escalated_at: datetime | None = None,
    ) -> CallSession:
        """A stored call placed in its state, refusing a record no legal sequence could produce."""
        if is_terminal(state) != (ended_at is not None):
            raise InvariantError("a stored call has an end time exactly when it has ended")
        if ended_at is not None and ended_at < started_at:
            raise InvariantError("a call cannot end before it started")
        present = [participant.role for participant in participants if participant.is_present]
        if len(present) != len(set(present)):
            raise InvariantError("a stored call has one role present twice")
        if escalated_at is not None and handling is not CallHandling.ASSISTANT:
            raise InvariantError("only a call the assistant took can have asked for the user")
        if escalated_at is not None and escalated_at < started_at:
            raise InvariantError("a call cannot ask for the user before it started")

        call = cls(id=id, user_id=user_id, caller=caller, started_at=started_at)
        call._state = state
        call._participants = list(participants)
        call.ended_at = ended_at
        call.handling = handling
        call.escalated_at = escalated_at
        return call

    @property
    def state(self) -> CallState:
        return self._state

    @property
    def is_over(self) -> bool:
        return is_terminal(self._state)

    @property
    def participants(self) -> tuple[Participant, ...]:
        return tuple(self._participants)

    @property
    def transcript(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._transcript)

    def move_to(self, state: CallState, *, at_instant: datetime | None = None) -> None:
        """Move the call, recording when it ends or first asks for the user, not before it began."""
        moved = move(self._state, state)
        if is_terminal(moved):
            if at_instant is None:
                raise InvariantError(
                    "a call that has ended must record when; its duration is read from it"
                )
            self.ended_at = max(at_instant, self.started_at)
        if moved is CallState.ESCALATION_REQUESTED:
            if at_instant is None:
                raise InvariantError(
                    "a call that asks for the user must record when; history shows it"
                )
            if self.escalated_at is None:
                self.escalated_at = max(at_instant, self.started_at)
        self.handling = _HANDLING_BY_STATE.get(moved, self.handling)
        self._state = moved

    def add_participant(self, role: ParticipantRole, at_instant: datetime) -> None:
        """Put somebody on the call, once per role at a time."""
        if self.is_over:
            raise InvariantError("nobody can join a call that has ended")
        if any(p.role is role and p.is_present for p in self._participants):
            raise InvariantError(f"the {role} is already on this call")
        self._participants.append(Participant(role, at_instant))

    def remove_participant(self, role: ParticipantRole, at_instant: datetime) -> None:
        """Take somebody off the call, refusing one who is not on it."""
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
