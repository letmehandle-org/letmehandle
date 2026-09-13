"""The aggregate must refuse what the state machine forbids, whatever a caller tries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import IllegalTransitionError, InvariantError
from letmehandle.domain.models.call import (
    CallHandling,
    CallSession,
    Participant,
    ParticipantRole,
    Speaker,
    TranscriptEntry,
)
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber

START = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def later(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def a_call() -> CallSession:
    return CallSession(
        id=CallId("call-1"), user_id=UserId("user-1"), caller=Caller(), started_at=START
    )


class TestState:
    def test_a_call_begins_received_and_unfinished(self) -> None:
        call = a_call()
        assert call.state is CallState.RECEIVED
        assert not call.is_over
        assert call.ended_at is None

    def test_a_legal_move_is_applied(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        assert call.state is CallState.ROUTING

    def test_an_illegal_move_is_refused_and_leaves_the_call_where_it_was(self) -> None:
        call = a_call()
        with pytest.raises(IllegalTransitionError):
            call.move_to(CallState.HUMAN_JOINED)
        assert call.state is CallState.RECEIVED

    def test_ending_a_call_requires_the_moment_it_ended(self) -> None:
        # The duration is the difference between two recorded moments. A missing one makes
        # every summary and every metric built on it wrong.
        call = a_call()
        call.move_to(CallState.ROUTING)
        with pytest.raises(InvariantError, match="when"):
            call.move_to(CallState.REJECTED)

    def test_a_finished_call_records_when_and_how_long(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.PASSTHROUGH)
        call.move_to(CallState.COMPLETED, at_instant=later(90))
        assert call.is_over
        assert call.ended_at == later(90)
        assert call.duration_seconds() == 90

    def test_duration_is_unknown_while_the_call_is_running(self) -> None:
        assert a_call().duration_seconds() is None

    def test_an_end_reported_before_the_start_is_recorded_as_the_start(self) -> None:
        # The start comes from the carrier and the end from this host's clock; a few
        # milliseconds of skew between them must not make the call unrecordable.
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.FAILED, at_instant=START - timedelta(milliseconds=5))
        assert call.ended_at == START
        assert call.duration_seconds() == 0

    def test_the_state_cannot_be_assigned_around_the_rules(self) -> None:
        # The whole reason the field is private: without this, any caller could put a call in
        # a state the machine forbids and nothing would notice until much later.
        call = a_call()
        with pytest.raises(AttributeError):
            call.state = CallState.COMPLETED  # type: ignore[misc]


class TestParticipants:
    def test_participants_join_and_leave(self) -> None:
        call = a_call()
        call.add_participant(ParticipantRole.CALLER, START)
        assert call.has_participant(ParticipantRole.CALLER)

        call.remove_participant(ParticipantRole.CALLER, later(60))
        assert not call.has_participant(ParticipantRole.CALLER)
        assert len(call.participants) == 1
        assert call.participants[0].left_at == later(60)

    def test_three_parties_can_be_present_at_once(self) -> None:
        # The product's central capability, expressed without a special case: the user is a
        # participant like any other.
        call = a_call()
        for role in ParticipantRole:
            call.add_participant(role, START)
        assert all(call.has_participant(role) for role in ParticipantRole)

    def test_a_role_cannot_join_twice(self) -> None:
        # Two agents or two humans would make "who is on this call" ambiguous, and every
        # question asked about the call afterwards with it.
        call = a_call()
        call.add_participant(ParticipantRole.AGENT, START)
        with pytest.raises(InvariantError, match="already"):
            call.add_participant(ParticipantRole.AGENT, later(1))

    def test_a_role_may_rejoin_after_leaving(self) -> None:
        # An escalation that is abandoned and retried is an ordinary sequence, not an error.
        call = a_call()
        call.add_participant(ParticipantRole.HUMAN, START)
        call.remove_participant(ParticipantRole.HUMAN, later(10))
        call.add_participant(ParticipantRole.HUMAN, later(20))
        assert call.has_participant(ParticipantRole.HUMAN)
        assert len(call.participants) == 2

    def test_removing_the_second_of_several_leaves_the_others_alone(self) -> None:
        # Exercises the search past a non-matching participant, which is the ordinary case
        # during an escalation: the caller is already on the call when the human leaves.
        call = a_call()
        call.add_participant(ParticipantRole.CALLER, START)
        call.add_participant(ParticipantRole.HUMAN, later(5))

        call.remove_participant(ParticipantRole.HUMAN, later(10))

        assert call.has_participant(ParticipantRole.CALLER)
        assert not call.has_participant(ParticipantRole.HUMAN)

    def test_removing_somebody_who_is_not_there_is_an_error(self) -> None:
        # Silence would hide a caller believing the call has a shape it does not.
        with pytest.raises(InvariantError, match="not on this call"):
            a_call().remove_participant(ParticipantRole.HUMAN, START)

    def test_nobody_joins_a_call_that_has_ended(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.REJECTED, at_instant=later(5))
        with pytest.raises(InvariantError):
            call.add_participant(ParticipantRole.HUMAN, later(6))

    def test_a_participant_cannot_leave_before_arriving(self) -> None:
        with pytest.raises(InvariantError):
            Participant(ParticipantRole.CALLER, later(10), later(1))


class TestTranscript:
    def test_utterances_are_recorded_in_order(self) -> None:
        call = a_call()
        call.record(Speaker.CALLER, "Is anyone there?", START)
        call.record(Speaker.AGENT, "Yes, how can I help?", later(2))
        assert [entry.speaker for entry in call.transcript] == [Speaker.CALLER, Speaker.AGENT]

    def test_an_empty_utterance_is_refused(self) -> None:
        with pytest.raises(InvariantError):
            TranscriptEntry(Speaker.CALLER, "   ", START)

    def test_nothing_is_said_after_the_call_ends(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.REJECTED, at_instant=later(5))
        with pytest.raises(InvariantError):
            call.record(Speaker.CALLER, "Hello?", later(6))

    def test_the_transcript_cannot_be_edited_through_the_property(self) -> None:
        # A tuple, so that a caller holding it cannot append to the call's own history.
        call = a_call()
        call.record(Speaker.CALLER, "Hello", START)
        assert isinstance(call.transcript, tuple)
        assert len(call.transcript) == 1


class TestRepresentation:
    def test_neither_a_call_nor_an_entry_prints_what_was_said_or_who_said_it(self) -> None:
        # A repr is what a debugger, a log line or a failing assertion prints.
        call = CallSession(
            id=CallId("call-1"),
            user_id=UserId("user-1"),
            caller=Caller(
                number=PhoneNumber.parse("+12025550123"), display_name="Wrenfield Parcel Desk"
            ),
            started_at=START,
        )
        call.record(Speaker.CALLER, "the gate code is violet-kestrel-5521", START)

        printed = repr(call) + repr(call.transcript[0])

        for secret in ("violet-kestrel", "5550123", "Wrenfield"):
            assert secret not in printed
        assert "call-1" in printed


class TestEscalationSequence:
    def test_the_whole_escalation_path_is_expressible(self) -> None:
        # Scenario B from the plan, at the level the domain is responsible for.
        call = a_call()
        call.add_participant(ParticipantRole.CALLER, START)
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        call.add_participant(ParticipantRole.AGENT, later(1))
        call.record(Speaker.CALLER, "I have a parcel for you.", later(5))

        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        call.move_to(CallState.HUMAN_RINGING)
        call.move_to(CallState.HUMAN_JOINED)
        call.add_participant(ParticipantRole.HUMAN, later(20))

        assert all(call.has_participant(role) for role in ParticipantRole)
        call.move_to(CallState.COMPLETED, at_instant=later(120))
        assert call.duration_seconds() == 120

    def test_an_unanswered_escalation_returns_the_call_to_the_agent(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        call.move_to(CallState.HUMAN_RINGING)
        call.move_to(CallState.AGENT_HANDLING)
        assert call.state is CallState.AGENT_HANDLING
        assert not call.is_over


class TestHowTheCallWasHandled:
    def test_a_call_put_through_is_recorded_as_passed_through(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        assert call.handling is None
        call.move_to(CallState.PASSTHROUGH)
        assert call.handling is CallHandling.PASSED_THROUGH

    def test_a_call_given_to_the_assistant_stays_the_assistants_through_escalation(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        call.move_to(CallState.HUMAN_RINGING)
        call.move_to(CallState.AGENT_HANDLING)
        assert call.handling is CallHandling.ASSISTANT

    def test_a_rejected_call_was_handled_by_nobody(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.REJECTED, at_instant=later(1))
        assert call.handling is None

    def test_asking_for_the_user_records_when_and_needs_the_moment(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        with pytest.raises(InvariantError, match="when"):
            call.move_to(CallState.ESCALATION_REQUESTED)
        assert call.state is CallState.AGENT_HANDLING
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        assert call.escalated_at == later(10)

    def test_a_second_escalation_keeps_when_the_user_was_first_asked_for(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(10))
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=later(40))
        assert call.escalated_at == later(10)

    def test_an_escalation_a_moment_before_the_start_is_recorded_at_the_start(self) -> None:
        call = a_call()
        call.move_to(CallState.ROUTING)
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.ESCALATION_REQUESTED, at_instant=START - timedelta(milliseconds=3))
        assert call.escalated_at == START


class TestRestore:
    def test_a_stored_call_comes_back_exactly(self) -> None:
        participants = (
            Participant(ParticipantRole.CALLER, START, later(60)),
            Participant(ParticipantRole.AGENT, later(1)),
        )
        call = CallSession.restore(
            id=CallId("call-1"),
            user_id=UserId("user-1"),
            caller=Caller(),
            started_at=START,
            state=CallState.COMPLETED,
            participants=participants,
            ended_at=later(60),
        )
        assert call.state is CallState.COMPLETED
        assert call.participants == participants
        assert call.duration_seconds() == 60
        assert call.transcript == ()
        assert call.handling is None
        assert call.escalated_at is None

    def test_how_it_was_handled_and_when_the_user_was_asked_for_come_back(self) -> None:
        call = CallSession.restore(
            id=CallId("call-1"),
            user_id=UserId("user-1"),
            caller=Caller(),
            started_at=START,
            state=CallState.HUMAN_RINGING,
            participants=(),
            ended_at=None,
            handling=CallHandling.ASSISTANT,
            escalated_at=later(5),
        )
        assert call.handling is CallHandling.ASSISTANT
        assert call.escalated_at == later(5)

    @pytest.mark.parametrize(
        ("handling", "escalated_at"),
        [
            (None, later(5)),
            (CallHandling.PASSED_THROUGH, later(5)),
            (CallHandling.ASSISTANT, START - timedelta(seconds=1)),
        ],
    )
    def test_an_escalation_no_legal_sequence_could_produce_is_refused(
        self, handling: CallHandling | None, escalated_at: datetime
    ) -> None:
        with pytest.raises(InvariantError):
            CallSession.restore(
                id=CallId("call-1"),
                user_id=UserId("user-1"),
                caller=Caller(),
                started_at=START,
                state=CallState.AGENT_HANDLING,
                participants=(),
                ended_at=None,
                handling=handling,
                escalated_at=escalated_at,
            )

    @pytest.mark.parametrize(
        ("state", "ended_at", "participants"),
        [
            (CallState.COMPLETED, None, ()),
            (CallState.AGENT_HANDLING, later(5), ()),
            (CallState.FAILED, START - timedelta(seconds=1), ()),
            (
                CallState.AGENT_HANDLING,
                None,
                (
                    Participant(ParticipantRole.AGENT, START),
                    Participant(ParticipantRole.AGENT, START),
                ),
            ),
        ],
    )
    def test_a_record_no_legal_sequence_could_produce_is_refused(
        self,
        state: CallState,
        ended_at: datetime | None,
        participants: tuple[Participant, ...],
    ) -> None:
        with pytest.raises(InvariantError):
            CallSession.restore(
                id=CallId("call-1"),
                user_id=UserId("user-1"),
                caller=Caller(),
                started_at=START,
                state=state,
                participants=participants,
                ended_at=ended_at,
            )


def test_a_departure_a_moment_before_the_join_is_recorded_at_the_join() -> None:
    # Joined by one clock and left by another, a few milliseconds apart. The departure happened, so
    # it is recorded, at the join rather than before it.
    joined = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    departed = Participant(ParticipantRole.CALLER, joined).departing(
        joined - timedelta(milliseconds=5)
    )
    assert departed.left_at == joined
