"""The summary written when a model's cannot be.

Every test is a call ending one way, walked through the real state machine, and the assertion is
what the user reads afterwards: an outcome that is true, a sentence that says it plainly, and
nothing the call did not contain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.application.calls.fallback import (
    SUMMARY_PHRASEBOOKS,
    CallFacts,
    SummaryPhrasebook,
    _every_phrasebook_is_complete,
    fallback_summary,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import CallSession, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome

START = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
CALLER_NUMBER = PhoneNumber("+12025550199")
STRANGER = Caller(number=CALLER_NUMBER, category=CallerCategory.UNKNOWN)
REASON = EscalationReason.CALLER_ASKED_FOR_THE_USER


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def a_call(caller: Caller = STRANGER) -> CallSession:
    call = CallSession(
        id=CallId("call-1"), user_id=UserId("user-1"), caller=caller, started_at=START
    )
    call.move_to(CallState.ROUTING)
    return call


def rejected(caller: Caller = STRANGER) -> CallSession:
    call = a_call(caller)
    call.move_to(CallState.REJECTED, at_instant=at(1))
    return call


def passed_through() -> CallSession:
    call = a_call()
    call.move_to(CallState.PASSTHROUGH)
    call.add_participant(ParticipantRole.HUMAN, at(2))
    call.move_to(CallState.COMPLETED, at_instant=at(60))
    return call


def handled_by_agent() -> CallSession:
    call = a_call()
    call.move_to(CallState.AGENT_HANDLING)
    call.add_participant(ParticipantRole.AGENT, at(1))
    call.move_to(CallState.COMPLETED, at_instant=at(45))
    return call


def escalated(
    *, answered: bool, joined_at: datetime | None = None, caller: Caller = STRANGER
) -> CallSession:
    call = a_call(caller)
    call.move_to(CallState.AGENT_HANDLING)
    call.add_participant(ParticipantRole.AGENT, at(1))
    call.move_to(CallState.ESCALATION_REQUESTED, at_instant=at(10))
    call.move_to(CallState.HUMAN_RINGING)
    if answered:
        call.move_to(CallState.HUMAN_JOINED)
        call.add_participant(ParticipantRole.HUMAN, joined_at or at(20))
    else:
        call.move_to(CallState.AGENT_HANDLING)
    call.move_to(CallState.COMPLETED, at_instant=at(90))
    return call


def failed_mid_call() -> CallSession:
    call = a_call()
    call.move_to(CallState.AGENT_HANDLING)
    call.add_participant(ParticipantRole.AGENT, at(1))
    call.move_to(CallState.FAILED, at_instant=at(30))
    return call


class TestEveryEnding:
    @pytest.mark.parametrize(
        ("facts", "outcome", "headline"),
        [
            (
                CallFacts(rejected()),
                CallOutcome.REJECTED_BY_RULE,
                "A call from an unknown caller was ended by your rules.",
            ),
            (
                CallFacts(passed_through()),
                CallOutcome.PASSED_THROUGH,
                "A call from an unknown caller was put straight through to you.",
            ),
            (
                CallFacts(handled_by_agent()),
                CallOutcome.RESOLVED_BY_AGENT,
                "Your assistant took a call from an unknown caller.",
            ),
            (
                CallFacts(handled_by_agent(), caller_hung_up=True),
                CallOutcome.CALLER_HUNG_UP,
                "A call from an unknown caller ended when the caller hung up.",
            ),
            (
                CallFacts(escalated(answered=True), escalation_reason=REASON),
                CallOutcome.HANDED_TO_USER,
                "Your assistant took a call from an unknown caller and handed it over to you.",
            ),
            (
                CallFacts(escalated(answered=False), escalation_reason=REASON),
                CallOutcome.UNANSWERED_ESCALATION,
                "Your assistant tried to reach you about a call from an unknown caller, "
                "but you did not answer.",
            ),
            (
                CallFacts(failed_mid_call()),
                CallOutcome.FAILED,
                "A call from an unknown caller could not be handled because something failed.",
            ),
        ],
        ids=lambda value: value.value if isinstance(value, CallOutcome) else None,
    )
    def test_the_outcome_and_headline_say_how_it_ended(
        self, facts: CallFacts, outcome: CallOutcome, headline: str
    ) -> None:
        summary = fallback_summary(facts, locale="en")

        assert summary.outcome is outcome
        assert summary.headline == headline
        assert summary.call_id == facts.call.id
        assert (summary.started_at, summary.ended_at) == (
            facts.call.started_at,
            facts.call.ended_at,
        )

    def test_every_outcome_the_domain_has_is_reachable(self) -> None:
        # A new outcome with no path here is one the fallback can never write, and a call ending
        # that way would be summarised as something else.
        reached = {
            fallback_summary(facts, locale="en").outcome
            for facts in (
                CallFacts(rejected()),
                CallFacts(passed_through()),
                CallFacts(handled_by_agent()),
                CallFacts(handled_by_agent(), caller_hung_up=True),
                CallFacts(escalated(answered=True), escalation_reason=REASON),
                CallFacts(escalated(answered=False), escalation_reason=REASON),
                CallFacts(failed_mid_call()),
            )
        }
        assert reached == set(CallOutcome)

    def test_a_missed_escalation_outranks_the_caller_giving_up(self) -> None:
        summary = fallback_summary(
            CallFacts(escalated(answered=False), escalation_reason=REASON, caller_hung_up=True),
            locale="en",
        )
        assert summary.outcome is CallOutcome.UNANSWERED_ESCALATION

    def test_a_caller_who_hangs_up_before_the_assistant_joins_was_not_put_through(self) -> None:
        # Given to the assistant, and gone before its leg joined: nobody put the call through.
        call = a_call()
        call.move_to(CallState.AGENT_HANDLING)
        call.move_to(CallState.COMPLETED, at_instant=at(3))
        summary = fallback_summary(CallFacts(call, caller_hung_up=True), locale="en")
        assert summary.outcome is CallOutcome.CALLER_HUNG_UP

    def test_a_call_the_user_joined_alone_is_passed_through_whatever_joined_it(self) -> None:
        call = a_call()
        call.move_to(CallState.PASSTHROUGH)
        call.move_to(CallState.COMPLETED, at_instant=at(3))
        summary = fallback_summary(CallFacts(call, caller_hung_up=True), locale="en")
        assert summary.outcome is CallOutcome.PASSED_THROUGH

    def test_a_call_still_in_progress_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="ended"):
            fallback_summary(CallFacts(a_call()), locale="en")


class TestTheUserJoining:
    def test_the_moment_they_joined_is_recorded_with_why_they_were_asked(self) -> None:
        summary = fallback_summary(
            CallFacts(escalated(answered=True), escalation_reason=REASON), locale="en"
        )
        assert summary.human_joined_at == at(20)
        assert summary.escalation_reason is REASON

    def test_answering_a_call_put_through_is_not_joining_one(self) -> None:
        summary = fallback_summary(CallFacts(passed_through()), locale="en")
        assert summary.human_joined_at is None
        assert not summary.human_joined

    def test_a_join_timed_before_the_call_by_clock_skew_is_recorded_at_its_start(self) -> None:
        call = escalated(answered=True, joined_at=START - timedelta(milliseconds=3))
        summary = fallback_summary(CallFacts(call, escalation_reason=REASON), locale="en")
        assert summary.human_joined_at == START


class TestWhatItSays:
    def test_nothing_is_extracted_and_nothing_is_classified_that_was_not(self) -> None:
        summary = fallback_summary(CallFacts(handled_by_agent()), locale="en")
        assert summary.details == ()
        assert summary.intent is CallIntent.UNDETERMINED

    def test_a_classification_the_call_did_reach_is_kept(self) -> None:
        facts = CallFacts(
            handled_by_agent(),
            intent=CallIntent.DELIVERY_IN_PROGRESS,
            importance=CallImportance.NOTABLE,
        )
        summary = fallback_summary(facts, locale="en")
        assert (summary.intent, summary.importance) == (
            CallIntent.DELIVERY_IN_PROGRESS,
            CallImportance.NOTABLE,
        )

    def test_a_contact_is_named(self) -> None:
        caller = Caller(
            number=CALLER_NUMBER, display_name="Sam", category=CallerCategory.KNOWN_CONTACT
        )
        summary = fallback_summary(CallFacts(rejected(caller)), locale="en")
        assert summary.headline == "A call from Sam was ended by your rules."

    def test_a_name_the_network_supplied_for_a_stranger_is_not_repeated(self) -> None:
        caller = Caller(
            number=CALLER_NUMBER, display_name="Prize Desk", category=CallerCategory.SALES
        )
        summary = fallback_summary(CallFacts(rejected(caller)), locale="en")
        assert summary.headline == "A call from a sales caller was ended by your rules."

    def test_the_number_never_appears(self) -> None:
        caller = Caller(number=CALLER_NUMBER, category=CallerCategory.KNOWN_CONTACT)
        summary = fallback_summary(CallFacts(rejected(caller)), locale="en")
        assert summary.headline == "A call from one of your contacts was ended by your rules."
        assert CALLER_NUMBER.value[-4:] not in summary.headline

    def test_a_withheld_number_says_so(self) -> None:
        summary = fallback_summary(CallFacts(rejected(Caller())), locale="en")
        assert summary.headline == (
            "A call from a caller who withheld their number was ended by your rules."
        )

    def test_a_contact_with_a_name_too_long_to_fit_is_described_instead(self) -> None:
        caller = Caller(
            display_name="S" * MAX_HEADLINE_CHARACTERS, category=CallerCategory.KNOWN_CONTACT
        )
        for facts in (
            CallFacts(rejected(caller)),
            CallFacts(escalated(answered=False, caller=caller), escalation_reason=REASON),
        ):
            summary = fallback_summary(facts, locale="en")
            assert len(summary.headline) <= MAX_HEADLINE_CHARACTERS
            assert "one of your contacts" in summary.headline

    @pytest.mark.parametrize("category", list(CallerCategory))
    def test_every_headline_for_every_caller_fits(self, category: CallerCategory) -> None:
        book = SUMMARY_PHRASEBOOKS["en"]
        for template in book.headline.values():
            for who in (book.caller[category], book.withheld):
                assert len(template.format(caller=who)) <= MAX_HEADLINE_CHARACTERS

    def test_a_locale_with_no_phrasing_of_its_own_is_written_in_english(self) -> None:
        in_english = fallback_summary(CallFacts(rejected()), locale="en")
        assert fallback_summary(CallFacts(rejected()), locale="fr-FR").headline == (
            in_english.headline
        )


class TestAfterTheTranscriptIsGone:
    def test_it_needs_nothing_but_the_call_to_render(self) -> None:
        # Built from the stored call alone, as it is read back once every line said on it has
        # been purged: no transcript, restored from storage, and still a complete summary.
        original = escalated(answered=True)
        stored = CallSession.restore(
            id=original.id,
            user_id=original.user_id,
            caller=original.caller,
            started_at=original.started_at,
            state=original.state,
            participants=original.participants,
            ended_at=original.ended_at,
        )
        assert stored.transcript == ()

        summary = fallback_summary(CallFacts(stored, escalation_reason=REASON), locale="en")

        assert summary.outcome is CallOutcome.HANDED_TO_USER
        assert summary.headline.strip()
        assert summary.duration_seconds == 90


class TestThePhrasebooks:
    def test_a_phrasebook_missing_an_outcome_stops_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        english = SUMMARY_PHRASEBOOKS["en"]
        incomplete = SummaryPhrasebook(
            headline={
                outcome: text
                for outcome, text in english.headline.items()
                if outcome is not CallOutcome.FAILED
            },
            caller=english.caller,
            withheld=english.withheld,
        )
        monkeypatch.setattr(
            "letmehandle.application.calls.fallback.SUMMARY_PHRASEBOOKS", {"xx": incomplete}
        )
        with pytest.raises(
            InvariantError, match="xx summary phrasebook has no phrasing for failed"
        ):
            _every_phrasebook_is_complete()
