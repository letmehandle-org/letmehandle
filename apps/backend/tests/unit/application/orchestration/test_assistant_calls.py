"""Calls the assistant takes: the conversation, the judgement, escalation and de-escalation.

A streaming line only: on a handset's line none of this exists, which the tests over every line
assert from the other side.
"""

from __future__ import annotations

import asyncio
from datetime import time
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.ports import CallEnding, OutcomeRecord
from letmehandle.application.orchestration.run import CallIsOverError
from letmehandle.application.orchestration.summary import MESSAGE_LABEL
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFrame
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.call import CallHandling, ParticipantRole, Speaker
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.escalation_context import EscalationStatus
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    ImportantContact,
    TimeWindow,
    UserPreferences,
)
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.domain.ports.call_transport import ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.domain.ports.speech import (
    AudioProduced,
    SessionFailed,
    SpeechEnded,
    TranscriptProduced,
)
from tests.support.orchestration import (
    OWNERS_NUMBER,
    QUICK,
    ROUTINE,
    WANTS_THE_USER,
    Look,
    Running,
    StreamingLine,
    WritingSummariser,
    eventually,
    orchestrating,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.call import TranscriptEntry

CALL = "call"
STRANGER_NUMBER = PhoneNumber("+12025550101")
STRANGER = Caller(number=STRANGER_NUMBER)
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


class TestTheUsersHours:
    """The orchestrator routes on the domain's policy, hours included (D-030)."""

    @staticmethod
    def answering(start: int, end: int) -> UserPreferences:
        # The orchestrator's clock reads noon, UTC.
        return UserPreferences(
            rules=CallRules(active_hours=TimeWindow(time(start), time(end), "UTC"))
        )

    async def test_a_call_outside_the_assistants_hours_rings_the_user(self) -> None:
        line = streaming()
        async with orchestrating(line, preferences=self.answering(13, 18)) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)

            assert line.dialled == [OWNERS_NUMBER]
            assert line.asked("answer", CALL) == 0
            assert running.speech.sessions == []

    async def test_a_call_inside_the_assistants_hours_is_the_assistants(self) -> None:
        line = streaming()
        async with orchestrating(line, preferences=self.answering(9, 18)) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.AGENT_HANDLING)

            assert line.asked("answer", CALL) == 1
            assert line.dialled == []


class TestTheSummary:
    """Written by the summariser for a call the assistant took, and by the facts otherwise."""

    async def test_a_call_the_assistant_took_is_summarised_with_what_the_agent_judged(
        self,
    ) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await with_the_assistant(running)
            await running.caller_says("Is she there? It is urgent.")
            await eventually(lambda: running.judgements == 1)
            line.hangs_up(CALL)
            await running.ended(CALL)

            [(facts, transcript)] = running.summariser.asked
            assert facts.intent is CallIntent.PERSONAL
            assert facts.importance is CallImportance.URGENT
            assert [each.text for each in transcript] == ["Is she there? It is urgent."]
            summary = running.stores.summaries.stored[CallId(CALL)]
            # The model writes the headline; the outcome stays the facts'.
            assert summary.headline == WritingSummariser.HEADLINE
            assert summary.outcome is CallOutcome.UNANSWERED_ESCALATION

    async def test_a_call_put_straight_through_is_summarised_from_its_facts(self) -> None:
        line = streaming()
        passing = UserPreferences(rules=CallRules(active_hours=TimeWindow(time(0), time(1), "UTC")))
        async with orchestrating(line, preferences=passing) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)
            line.hangs_up(CALL)
            await running.ended(CALL)

            assert running.summariser.asked == []
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.headline != WritingSummariser.HEADLINE

    async def test_an_important_contact_is_recorded_and_summarised_under_the_user_s_label(
        self,
    ) -> None:
        line = streaming()
        contact = ImportantContact(number=STRANGER_NUMBER, label="Aunt May")
        async with orchestrating(
            line, preferences=UserPreferences(important_contacts=(contact,))
        ) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.PASSTHROUGH)
            line.hangs_up(CALL)
            call = await running.ended(CALL)

            assert call.caller.category is CallerCategory.KNOWN_CONTACT
            assert call.caller.display_name == "Aunt May"
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert "Aunt May" in summary.headline

    async def test_a_summariser_that_never_answers_cannot_hold_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            running.summariser.hanging = True
            await with_the_assistant(running)
            await running.caller_says("Just checking in.")
            line.hangs_up(CALL)
            # Let go at the transport before anybody waits on a summary.
            await eventually(lambda: bool(running.summariser.asked))
            assert line.asked("terminate", CALL) == 1

            call = await running.ended(CALL)

            assert call.state is CallState.COMPLETED
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.CALLER_HUNG_UP
            assert summary.headline != WritingSummariser.HEADLINE
            assert running.metrics.counted("call.summary_failed") == 1

    async def test_stopping_is_not_held_up_by_a_summariser_that_never_answers(self) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            running.summariser.hanging = True
            await with_the_assistant(running)
            await running.caller_says("Still there?")

            async with asyncio.timeout(QUICK.shutdown.total_seconds()):
                await running.orchestrator.stop()

            assert running.stores.call(CALL).state is CallState.FAILED
            assert CallId(CALL) in running.stores.summaries.stored


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

    async def test_a_hindi_users_call_opens_in_hindi_with_a_voice_that_speaks_it(self) -> None:
        async with orchestrating(
            streaming(), preferences=UserPreferences(locale="hi-IN")
        ) as running:
            running.speech.languages = ("en", "hi")
            await with_the_assistant(running)
            connection = running.speech.connections[0]
            assert connection["locale"] == "hi-IN"
            assert connection["voice_id"] == "gentle"
            assert str(connection["greeting"]).startswith("नमस्ते")

    async def test_a_language_the_speech_service_cannot_speak_opens_the_call_in_english(
        self,
    ) -> None:
        async with orchestrating(
            streaming(), preferences=UserPreferences(locale="hi-IN")
        ) as running:
            await with_the_assistant(running)
            connection = running.speech.connections[0]
            assert connection["locale"] == "en"
            assert connection["voice_id"] == "calm"
            assert connection["greeting"] == "Hello, how can I help?"
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING

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

    async def test_the_agent_ending_the_call_lets_the_assistant_finish_its_goodbye(self) -> None:
        judged = asyncio.Event()
        line = streaming()
        looks = [Look(ending=CallEnding.RESOLVED, waits_for=judged)]
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("That's all, thank you.")
            await eventually(lambda: running.judgements == 1)
            speaker = line.speakers[CallId(CALL)]
            speaker.hold()
            session = await running.session()
            await session.emit(AudioProduced(AudioFrame(b"\x00\x10" * 320, SPEECH_WIDEBAND)))
            await session.emit(
                TranscriptProduced("Thank you, goodbye.", speaker_is_caller=False, is_final=True)
            )
            await eventually(lambda: speaker.playing)
            judged.set()
            # Asked to end while the goodbye is still playing: the caller hears it out.
            await asyncio.sleep(QUICK.goodbye.total_seconds() / 5)
            assert line.asked("terminate", CALL) == 0

            speaker.release()
            call = await running.ended(CALL)

            assert call.state is CallState.COMPLETED
            assert line.asked("terminate", CALL) == 1
            said = running.stores.transcripts.lines[CallId(CALL)]
            assert [(each.speaker, each.text) for each in said][-1] == (
                Speaker.AGENT,
                "Thank you, goodbye.",
            )

    async def test_a_goodbye_that_never_ends_does_not_hold_the_call(self) -> None:
        judged = asyncio.Event()
        line = streaming()
        looks = [Look(ending=CallEnding.RESOLVED, waits_for=judged)]
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("Bye.")
            await eventually(lambda: running.judgements == 1)
            speaker = line.speakers[CallId(CALL)]
            speaker.hold()
            session = await running.session()
            await session.emit(AudioProduced(AudioFrame(b"\x00\x10" * 320, SPEECH_WIDEBAND)))
            await eventually(lambda: speaker.playing)
            judged.set()

            call = await running.ended(CALL)

            assert call.state is CallState.COMPLETED
            assert line.asked("terminate", CALL) == 1

    async def test_teardown_stops_the_assistant_before_storing_its_last_lines(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        judged = asyncio.Event()
        line = streaming()
        looks = [Look(ending=CallEnding.RESOLVED, waits_for=judged)]
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("Bye.")
            await eventually(lambda: running.judgements == 1)
            speaker = line.speakers[CallId(CALL)]
            speaker.hold()
            session = await running.session()
            await session.emit(AudioProduced(AudioFrame(b"\x00\x10" * 320, SPEECH_WIDEBAND)))
            await session.emit(
                TranscriptProduced("Goodbye.", speaker_is_caller=False, is_final=True)
            )
            await eventually(lambda: speaker.playing)
            transcripts = running.stores.transcripts
            store = transcripts.append
            closed_when_stored: list[bool] = []

            async def append(
                user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
            ) -> None:
                closed_when_stored.append(session.is_closed)
                await store(user_id, call_id, entries)

            monkeypatch.setattr(transcripts, "append", append)
            judged.set()
            speaker.release()
            await running.ended(CALL)

            assert closed_when_stored == [True]

    async def test_the_agent_ending_the_call_keeps_what_it_assessed_the_call_to_be(self) -> None:
        looks = [Look(proposal=ROUTINE, ending=CallEnding.RESOLVED)]
        line = streaming()
        async with orchestrating(line, looks=looks) as running:
            await with_the_assistant(running)
            await running.caller_says("Just a question about opening hours.")
            await running.ended(CALL)

            # The look that ended the call is torn down with it, and its reading is not lost.
            [(facts, _)] = running.summariser.asked
            assert facts.intent is CallIntent.ENQUIRY
            assert running.stores.summaries.stored[CallId(CALL)].intent is CallIntent.ENQUIRY

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

    async def test_a_record_claiming_an_ending_the_call_did_not_have_is_not_the_summary(
        self,
    ) -> None:
        # Written while the user's phone was ringing, anticipating a handover that never came: the
        # caller hung up first, and history must say the user was wanted and missed it.
        record = OutcomeRecord(CallOutcome.HANDED_TO_USER, "Handed the neighbour over to you.")
        line = streaming()
        looks = [Look(proposal=WANTS_THE_USER, record=record)]
        async with orchestrating(line, looks=looks) as running:
            await ringing(running)
            line.hangs_up(CALL)
            await running.ended(CALL)

            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.UNANSWERED_ESCALATION
            assert summary.human_joined_at is None
            assert summary.headline == WritingSummariser.HEADLINE

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
    async def test_a_user_answering_before_the_assistants_join_is_heard_is_recorded_after_it(
        self,
    ) -> None:
        # Callbacks can arrive in any order. The assistant was talking to the caller before the
        # user was rung, so the record says it was on the call first whatever order they came in.
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            line.arrives(CALL, STRANGER)
            await running.settled(CALL, CallState.AGENT_HANDLING)
            await running.session()
            await running.caller_says("Can I speak to them, please?")
            await running.settled(CALL, CallState.HUMAN_RINGING)
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            line.assistant_joins(CALL)
            await asyncio.sleep(0.02)

            assert [each.role for each in running.stores.call(CALL).participants] == [
                ParticipantRole.AGENT,
                ParticipantRole.HUMAN,
            ]

    async def test_the_user_is_told_why_they_are_rung_in_their_own_language(self) -> None:
        line = streaming()
        async with orchestrating(
            line, preferences=UserPreferences(locale="hi-IN"), looks=[Look(proposal=WANTS_THE_USER)]
        ) as running:
            await ringing(running)
            await eventually(lambda: bool(running.notifications.sent))
            [(_, notification)] = running.notifications.sent
            assert notification.title == "कॉल करने वाले ने आपसे बात करनी चाही"
            line.hangs_up(CALL)
            await running.ended(CALL)

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

    async def test_the_assistant_is_told_the_user_is_being_reached_before_they_are_dialled(
        self,
    ) -> None:
        # The assistant answers the caller while the dial is still on its way; told only once the
        # phone rang, it had already said the user could not be called.
        line = streaming()
        line.holding["dial"] = asyncio.Event()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await with_the_assistant(running)
            session = await running.session()
            await running.caller_says("It is very urgent, call them now.")
            await eventually(lambda: line.asked("dial", CALL) == 1)

            assert [each for each in session.context_updates if '"being_reached"' in each]
            line.holding["dial"].set()
            await running.settled(CALL, CallState.HUMAN_RINGING)
            assert sum('"being_reached"' in each for each in session.context_updates) == 1
            line.hangs_up(CALL)
            await running.ended(CALL)

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
            # Told it was checking, then told it did not get through, so it stops saying so.
            session = await running.session()
            told = [each for each in session.context_updates if '"user"' in each]
            assert '"being_reached"' in told[0]
            assert '"not_reached"' in told[-1]
            assert '"failed"' in told[-1]
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

    async def test_handing_over_to_a_user_on_the_way_keeps_the_assistant_until_they_join(
        self,
    ) -> None:
        line = streaming()
        looks = [Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER)]
        async with orchestrating(line, looks=looks) as running:
            await ringing(running)
            session = await running.session()
            await eventually(lambda: '"being_reached"' in "".join(session.context_updates))
            await running.caller_says("Are they coming?")
            await eventually(lambda: running.judgements == 2)
            # Still ringing, and the caller still has somebody to talk to.
            assert running.stores.call(CALL).state is CallState.HUMAN_RINGING
            assert not session.is_closed
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            await eventually(lambda: session.is_closed)
            line.hangs_up(CALL)
            call = await running.ended(CALL)
            assert call.state is CallState.COMPLETED

    async def test_handing_over_once_the_user_has_joined_lets_the_assistant_go(self) -> None:
        line = streaming()
        released = asyncio.Event()
        looks = [
            Look(proposal=WANTS_THE_USER),
            Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER, waits_for=released),
        ]
        async with orchestrating(line, looks=looks) as running:
            await ringing(running)
            await running.caller_says("I will wait.")
            await eventually(lambda: running.judgements == 2)
            line.user_answers(CALL)
            await running.settled(CALL, CallState.HUMAN_JOINED)
            session = await running.session()
            assert not session.is_closed
            # The look taken while the phone rang concludes with the user already on the call.
            released.set()
            await eventually(lambda: session.is_closed)
            assert running.stores.call(CALL).state is CallState.HUMAN_JOINED

    async def test_with_the_assistant_gone_a_user_not_reached_ends_the_call(self) -> None:
        line = streaming()
        async with orchestrating(line, looks=[Look(proposal=WANTS_THE_USER)]) as running:
            await ringing(running)
            session = await running.session()
            await session.emit(SessionFailed("gone", retryable=True))
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
                lambda: running.metrics.counted("call.judgement_failed", kind="unavailable") == 1
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

    async def test_the_assistant_leaving_before_the_hang_up_is_reported_waits_for_it(
        self,
    ) -> None:
        line = streaming()
        async with orchestrating(line) as running:
            await with_the_assistant(running)
            # A caller hanging up ends the conference, and the assistant's leg can be heard of
            # leaving before the caller's.
            line.leaves(CALL, Leg.ASSISTANT)
            session = await running.session()
            await eventually(lambda: session.is_closed)
            assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
            line.hangs_up(CALL)
            assert (await running.ended(CALL)).state is CallState.COMPLETED
            summary = running.stores.summaries.stored[CallId(CALL)]
            assert summary.outcome is CallOutcome.CALLER_HUNG_UP

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
