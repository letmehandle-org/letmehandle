"""Calls the assistant takes: the conversation, the judgement, escalation and de-escalation.

A streaming line only: on a handset's line none of this exists, which the tests over every line
assert from the other side.
"""

from __future__ import annotations

import asyncio

import pytest

from letmehandle.application.agent.ports import CallEnding, OutcomeRecord
from letmehandle.application.orchestration.run import CallIsOverError
from letmehandle.application.orchestration.summary import MESSAGE_LABEL
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.call import CallHandling, ParticipantRole, Speaker
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.escalation_context import EscalationStatus
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.domain.ports.call_transport import ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.domain.ports.speech import SessionFailed, SpeechEnded, TranscriptProduced
from tests.support.orchestration import (
    OWNERS_NUMBER,
    WANTS_THE_USER,
    Look,
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))
MESSAGES_ALLOWED = UserPreferences(authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE))


async def with_the_assistant(running: Running, call: str = CALL) -> None:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(call, STRANGER)
    await running.settled(call, CallState.AGENT_HANDLING)
    await running.session()
    line.assistant_joins(call)
    await eventually(lambda: running.stores.call(call).has_participant(ParticipantRole.AGENT))


async def ringing(running: Running) -> None:
    await with_the_assistant(running)
    await running.caller_says("Can I speak to them, please?")
    await running.settled(CALL, CallState.HUMAN_RINGING)


def streaming() -> StreamingLine:
    return StreamingLine()


class TestTheAssistantHandlesACall:
    async def test_a_call_is_answered_talked_on_judged_and_ended_by_the_caller(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            await running.caller_says("Hello, is this the right number?")
            await eventually(lambda: running.judgements == 1)
            line.hangs_up(CALL)
            call = await running.ended(CALL)
            session = await running.session()

            assert running.stores.states(CALL) == [
                CallState.RECEIVED,
                CallState.ROUTING,
                CallState.AGENT_HANDLING,
                CallState.COMPLETED,
            ]
            assert call.handling is CallHandling.ASSISTANT
            assert line.asked("answer", CALL) == 1
            assert line.asked("terminate", CALL) == 1
            assert session.is_closed
            assert running.agent.forgotten == [CallId(CALL)]
            said = running.stores.transcripts.lines[CallId(CALL)]
            assert [(line.speaker, line.text) for line in said] == [
                (Speaker.CALLER, "Hello, is this the right number?")
            ]
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.CALLER_HUNG_UP
            assert summary.intent is CallIntent.ENQUIRY

    async def test_the_assistant_is_told_the_user_and_speaks_in_their_voice(self) -> None:
        async with orchestrating(streaming()) as running:
            await with_the_assistant(running)
            connection = running.speech.connections[0]
            assert connection["voice_id"] == "calm"
            assert '"user": "not_asked"' in str(connection["system_context"])

    async def test_what_the_assistant_says_is_kept_and_does_not_start_a_judgement(self) -> None:
        async with orchestrating(streaming()) as running:
            await with_the_assistant(running)
            session = await running.session()
            await session.emit(
                TranscriptProduced("How can I help?", speaker_is_caller=False, is_final=True)
            )
            await eventually(lambda: bool(running.stores.transcripts.lines[CallId(CALL)]))
            assert running.agent.built is not None
            assert running.agent.built.judged == []

    async def test_the_agent_ending_the_call_writes_its_own_summary_with_the_messages(self) -> None:
        record = OutcomeRecord(CallOutcome.RESOLVED_BY_AGENT, "Took a message about the parcel.")
        looks = [
            Look(record=record, message="Leave it with the neighbour.", ending=CallEnding.RESOLVED)
        ]
        line = streaming()
        async with orchestrating(line, preferences=MESSAGES_ALLOWED, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("I have a parcel.")
            call = await running.ended(CALL)

            assert call.state is CallState.COMPLETED
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.RESOLVED_BY_AGENT
            assert summary.headline == "Took a message about the parcel."
            assert summary.detail(MESSAGE_LABEL) is not None
            assert line.asked("terminate", CALL) == 1

    async def test_a_record_the_domain_refuses_falls_back_to_the_facts(self) -> None:
        record = OutcomeRecord(CallOutcome.RESOLVED_BY_AGENT, "x" * 400)
        async with orchestrating(
            streaming(), looks=[Look(record=record, ending=CallEnding.RESOLVED)]
        ) as running:
            await with_the_assistant(running)
            await running.caller_says("Thanks, bye.")
            await running.ended(CALL)
            assert (
                running.stores.summaries.stored[CallId(CALL)].outcome
                is CallOutcome.RESOLVED_BY_AGENT
            )
            assert running.stores.summaries.stored[CallId(CALL)].headline != "x" * 400

    async def test_a_record_is_not_the_summary_of_a_call_that_failed(self) -> None:
        record = OutcomeRecord(CallOutcome.RESOLVED_BY_AGENT, "All sorted.")
        async with orchestrating(streaming(), looks=[Look(record=record)]) as running:
            await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(lambda: running.judgements == 1)
            running.line.report_failure(CALL)
            await running.ended(CALL)
            assert running.stores.summaries.stored[CallId(CALL)].outcome is CallOutcome.FAILED

    async def test_a_judgement_asked_for_while_one_runs_follows_it(self) -> None:
        gate = asyncio.Event()
        async with orchestrating(streaming(), looks=[Look(waits_for=gate)]) as running:
            await with_the_assistant(running)
            await running.caller_says("One.")
            await running.caller_says("Two.")
            await running.caller_says("Three.")
            await eventually(lambda: len(running.stores.transcripts.lines[CallId(CALL)]) == 3)
            gate.set()
            agent = running.agent.built
            assert agent is not None
            await eventually(lambda: len(agent.judged) == 2)
            # One look at the call as it stood, then one more at everything said meanwhile.
            assert [len(each.transcript) for each in agent.judged] == [1, 3]


class TestEscalation:
    async def test_an_escalation_answered_joins_the_user_and_ends_when_they_leave(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            session = await running.session()
            await eventually(lambda: any("on_the_call" in each for each in session.context_updates))
            line.leaves(CALL, Leg.USER)
            call = await running.ended(CALL)
            await running.quiet()

            assert running.stores.states(CALL) == [
                CallState.RECEIVED,
                CallState.ROUTING,
                CallState.AGENT_HANDLING,
                CallState.ESCALATION_REQUESTED,
                CallState.HUMAN_RINGING,
                CallState.HUMAN_JOINED,
                CallState.COMPLETED,
            ]
            assert line.dialled == [OWNERS_NUMBER]
            assert call.escalated_at is not None
            assert '"being_reached"' in session.context_updates[0]
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.HANDED_TO_USER
            assert summary.escalation_reason is EscalationReason.CALLER_ASKED_FOR_THE_USER
            assert summary.human_joined_at is not None
            # Told on the phone, with the context stored, and marked over when the call was.
            context = running.escalations.contexts.stored[(call.user_id, call.id)]
            assert context.established == "Asked for the user by name."
            assert context.status is EscalationStatus.ENDED
            assert len(running.notifications.sent) == 1

    @pytest.mark.parametrize(
        "outcome",
        [
            ParticipantOutcome.NO_ANSWER,
            ParticipantOutcome.BUSY,
            ParticipantOutcome.FAILED,
            ParticipantOutcome.ANSWERED_BY_MACHINE,
        ],
    )
    async def test_a_user_not_reached_hands_the_call_back_to_the_assistant(
        self, outcome: ParticipantOutcome
    ) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            line.user_unreachable(CALL, outcome)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            session = await running.session()
            await eventually(lambda: any(outcome.value in each for each in session.context_updates))

            assert '"not_reached"' in session.context_updates[-1]
            assert line.asked("cancel", CALL) == 0
            line.hangs_up(CALL)
            await running.ended(CALL)
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.UNANSWERED_ESCALATION

    async def test_a_ring_that_runs_out_is_cancelled_and_the_call_handed_back(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            session = await running.session()
            await eventually(lambda: any("no_answer" in each for each in session.context_updates))
            assert line.asked("cancel", CALL) == 1

    async def test_a_dial_the_transport_refuses_leaves_the_assistant_and_a_later_look_retries(
        self,
    ) -> None:
        line = streaming()
        line.refusing.add("dial")
        looks = [Look(proposal=WANTS_THE_USER), Look(proposal=WANTS_THE_USER)]
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("Put them on.")
            await eventually(lambda: running.metrics.counted("call.judgement_failed") == 1)
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            assert running.stores.states(CALL)[-2:] == [
                CallState.ESCALATION_REQUESTED,
                CallState.AGENT_HANDLING,
            ]
            # The failed ring left no mark, so the next look can ring.
            line.refusing.clear()
            await running.caller_says("Please, it is urgent.")
            await running.settled(CALL, CallState.HUMAN_RINGING)
            assert len(line.dialled) == 2

    async def test_a_notification_that_cannot_be_sent_never_prevents_the_ring(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            running.escalations.fail_on_opening.add(1)
            await ringing(running)
            await running.quiet()
            assert line.dialled == [OWNERS_NUMBER]
            assert (
                running.metrics.counted("escalation.dispatch", outcome="storage_unavailable") == 1
            )

    async def test_a_notification_still_in_flight_does_not_delay_the_ring(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            running.notifications.released.clear()
            await ringing(running)
            assert line.dialled == [OWNERS_NUMBER]
            assert running.notifications.sent == []
            running.notifications.released.set()
            await running.quiet()
            assert len(running.notifications.sent) == 1

    async def test_asking_again_while_the_user_rings_rings_nothing_more(self) -> None:
        line = streaming()
        upgrade = EscalationProposal(
            importance=CallImportance.URGENT,
            intent=CallIntent.PERSONAL,
            needs_the_users_decision=True,
        )
        async with orchestrating(
            line, looks=[Look(proposal=WANTS_THE_USER), Look(proposal=upgrade)]
        ) as running:
            await ringing(running)
            await running.caller_says("Hurry, please.")
            agent = running.agent.built
            assert agent is not None
            await eventually(lambda: len(agent.judged) == 2)
            assert line.dialled == [OWNERS_NUMBER]

    async def test_handing_over_to_a_user_on_the_way_leaves_the_call_standing(self) -> None:
        line = streaming()
        looks = [Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER)]
        async with orchestrating(line, looks=looks) as running:
            await ringing(running)
            session = await running.session()
            await eventually(lambda: session.is_closed)
            assert running.stores.call(CALL).state is CallState.HUMAN_RINGING
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            line.hangs_up(CALL)
            await running.ended(CALL)

    async def test_with_the_assistant_gone_a_user_not_reached_ends_the_call(self) -> None:
        line = streaming()
        looks = [Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER)]
        async with orchestrating(line, looks=looks) as running:
            await ringing(running)
            session = await running.session()
            await eventually(lambda: session.is_closed)
            line.user_unreachable(CALL, ParticipantOutcome.BUSY)
            call = await running.ended(CALL)
            assert call.state is CallState.COMPLETED


class TestDegradedProviders:
    async def test_a_speech_session_that_fails_mid_call_fails_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            session = await running.session()
            await session.emit(SessionFailed("the service went away", retryable=False))
            call = await running.ended(CALL)
            assert call.state is CallState.FAILED
            assert line.asked("terminate", CALL) == 1
            assert running.stores.summaries.stored[call.id].outcome is CallOutcome.FAILED

    async def test_speech_failing_while_the_user_rings_leaves_the_ring_going(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            session = await running.session()
            await session.emit(SessionFailed("gone", retryable=True))
            await eventually(lambda: session.is_closed)
            assert running.stores.call(CALL).state is CallState.HUMAN_RINGING
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)

    async def test_a_speech_service_that_will_not_open_fails_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            running.speech.refusing = True
            line.arrives(CALL, STRANGER)
            call = await running.ended(CALL)
            assert call.state is CallState.FAILED
            assert running.metrics.counted("call.provider_failed", stage="speech") == 1

    async def test_a_speech_service_that_never_opens_is_given_up_on_in_time(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            running.speech.never_answering = True
            line.arrives(CALL, STRANGER)
            call = await running.ended(CALL)
            assert call.state is CallState.FAILED
            assert (
                running.metrics.counted("call.provider_failed", stage="speech", kind="timeout") == 1
            )

    @pytest.mark.parametrize("refused", [True, False], ids=["refused", "hangs"])
    async def test_a_call_the_transport_will_not_answer_fails(self, refused: bool) -> None:
        line = streaming()
        if refused:
            line.refusing.add("answer")
        else:
            line.holding["answer"] = asyncio.Event()
        async with orchestrating(line) as running:
            line.arrives(CALL, STRANGER)
            call = await running.ended(CALL)
            assert call.state is CallState.FAILED
            assert running.speech.sessions == []

    async def test_an_unavailable_model_leaves_the_assistant_talking(self) -> None:
        line = streaming()
        looks = [Look(fails=ProviderError("model", "unreachable", retryable=True))]
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(
                lambda: running.metrics.counted("call.judgement_failed", kind="error") == 1
            )
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            session = await running.session()
            assert not session.is_closed

    async def test_a_judgement_that_takes_too_long_is_abandoned_and_the_call_goes_on(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(waits_for=asyncio.Event())]) as running:
            await with_the_assistant(running)
            await running.caller_says("Hello?")
            await eventually(
                lambda: running.metrics.counted("call.judgement_failed", kind="timeout") == 1
            )
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING

    async def test_the_assistant_leg_never_joining_fails_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            line.report_unreachable_assistant(CALL)
            call = await running.ended(CALL)
            assert call.state is CallState.FAILED

    async def test_the_assistant_leaving_while_the_user_is_on_leaves_the_call_standing(
        self,
    ) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            line.leaves(CALL, Leg.ASSISTANT)
            session = await running.session()
            await eventually(lambda: session.is_closed)
            assert running.stores.call(CALL).state is CallState.HUMAN_JOINED
            assert not running.stores.call(CALL).has_participant(ParticipantRole.AGENT)

    async def test_the_assistant_leaving_while_alone_with_the_caller_fails_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            line.leaves(CALL, Leg.ASSISTANT)
            assert (await running.ended(CALL)).state is CallState.FAILED

    async def test_audio_that_stops_before_the_hang_up_is_reported_waits_for_it(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            line.audio[CallId(CALL)].stop()
            await eventually(lambda: running.stores.call(CALL).state is CallState.AGENT_HANDLING)
            line.hangs_up(CALL)
            assert (await running.ended(CALL)).state is CallState.COMPLETED

    async def test_audio_that_stops_with_no_hang_up_fails_the_call_in_time(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            line.audio[CallId(CALL)].stop()
            assert (await running.ended(CALL)).state is CallState.FAILED

    async def test_a_speech_session_that_simply_ends_is_the_assistant_gone(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            session = await running.session()
            await session.close()
            assert (await running.ended(CALL)).state is CallState.FAILED

    async def test_telling_the_assistant_what_changed_can_fail_without_ending_anything(
        self,
    ) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            running.speech.refusing_updates = True
            await with_the_assistant(running)
            await running.caller_says("Get them.")
            await running.settled(CALL, CallState.HUMAN_RINGING)

    async def test_storage_that_stops_answering_mid_call_does_not_end_it(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            running.stores.unavailable = True
            await running.caller_says("Hello?")
            await eventually(
                lambda: running.metrics.counted("call.storage_failed", stage="transcript") == 1
            )
            running.stores.unavailable = False
            line.hangs_up(CALL)
            assert (await running.ended(CALL)).state is CallState.COMPLETED

    async def test_a_speaker_event_that_is_not_a_line_changes_nothing(self) -> None:
        async with orchestrating(streaming()) as running:
            await with_the_assistant(running)
            session = await running.session()
            await session.emit(SpeechEnded(by_caller=True))
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING


class TestRequestsForCallsThatAreOver:
    async def test_an_action_for_a_call_that_has_gone_is_refused(self) -> None:
        async with orchestrating(streaming()) as running:
            agent = running.agent.built
            assert agent is not None
            actions = agent._actions
            decision = EscalationDecision.needed(
                EscalationReason.CALLER_ASKED_FOR_THE_USER, EscalationUrgency.IMMEDIATE
            )
            with pytest.raises(CallIsOverError):
                await actions.escalate(CallId("never"), decision)
