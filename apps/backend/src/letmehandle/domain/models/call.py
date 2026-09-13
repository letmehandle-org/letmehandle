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


class CallHandling(StrEnum):
    """Whom routing gave the call to, which its final state no longer says.

    A completed call was either put straight through to the user or taken by the assistant, and
    history tells the two apart. Read from the moves themselves rather than set beside them, so it
    cannot disagree with the path the call took. A rejected call was given to nobody.
    """

    PASSED_THROUGH = "passed_through"
    ASSISTANT = "assistant"


_HANDLING_BY_STATE: dict[CallState, CallHandling] = {
    CallState.PASSTHROUGH: CallHandling.PASSED_THROUGH,
    CallState.AGENT_HANDLING: CallHandling.ASSISTANT,
}


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
        # Never before they joined. The two instants can come from different clocks — a carrier's
        # and this host's — a few milliseconds apart, and a departure that happened must be
        # recordable; the call's own end is clamped the same way.
        return Participant(self.role, self.joined_at, max(at_instant, self.joined_at))


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One thing somebody said.

    Held in memory for the length of the call. Whether it is written down, and for how long, is
    a decision made outside the domain — see the retention decision in the architecture record.
    """

    speaker: Speaker
    # Out of the repr: a repr is what a log line or a failing assertion prints.
    text: str = field(repr=False)
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
    handling: CallHandling | None = field(default=None, init=False)
    # When the assistant first asked for the user. Kept apart from who joined, because a user
    # asked for and never reached is exactly the call history has to show.
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
        """A call read back from storage, exactly as it was written.

        For a storage adapter, and nothing else: it places the call in a state without walking
        the transitions, because the transitions were walked before the call was stored. What
        it still refuses is a record no sequence of legal moves could have produced — an ending
        with no moment, a moment with no ending, or one role present twice.

        The transcript is not restored here. It is stored apart, encrypted and on its own
        retention clock, and is read through its own repository.
        """
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
        """Read-only. Changing it goes through `move_to`, which applies the rules."""
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
        """Move the call, or raise naming both states.

        `at_instant` is required when moving to an ending, because a call's duration is the
        difference between two recorded moments and a missing one makes every later summary
        and metric wrong. It is required when asking for the user too, and the first time is
        kept: history shows when the user was first wanted.

        An end earlier than the start is recorded as the start. The two moments come from
        different clocks — the start from the carrier, the end from this host — and a few
        milliseconds of skew between them is ordinary. Refusing would leave a call that
        happened with no record at all, and storing the earlier moment would make every read
        of the history refuse it instead; a zero-length call is the honest nearest truth.
        """
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
