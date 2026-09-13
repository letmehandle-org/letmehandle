"""What a model's draft summary must be to be kept: short, plain, true to the call and its ending.

Every test is one draft of one ended call, and the assertion is which problems the checks find in
it. An empty answer is a draft the user would read; anything else is the fallback.
"""

from __future__ import annotations

import pytest

from letmehandle.application.agent.tools.outcome import MAX_DETAILS
from letmehandle.application.calls import summary_checks
from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summary_checks import (
    SUMMARY_VOCABULARIES,
    DraftProblem,
    SummaryVocabulary,
    _every_vocabulary_is_complete,
    names_the_ending,
    problems_with,
    vocabulary_for,
    words,
)
from letmehandle.application.calls.summary_draft import (
    DetailKind,
    DraftDetail,
    SummaryDraft,
    SummaryRequest,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import Speaker
from letmehandle.domain.models.intent import CallIntent
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome
from tests.support.ended_calls import Ending, caller_said, ended

COURIER = caller_said(
    "Hi, it's Swift Parcels. I've left the parcel with the neighbour at 14 Alder Close.",
    "The tracking reference is SP 58213, and the driver will come back on Friday after two.",
)
FINE = "Swift Parcels left your parcel next door, and your assistant noted where."


def request_for(
    said: tuple[tuple[Speaker, str], ...] = COURIER, ending: Ending = Ending.RESOLVED
) -> SummaryRequest:
    facts = ended(said, ending)
    return SummaryRequest(
        known=fallback_summary(facts, locale="en"), transcript=facts.call.transcript, locale="en"
    )


def draft(
    headline: str = FINE,
    *details: DraftDetail,
    outcome: CallOutcome = CallOutcome.RESOLVED_BY_AGENT,
) -> SummaryDraft:
    return SummaryDraft(
        headline=headline, intent=CallIntent.DELIVERY_IN_PROGRESS, outcome=outcome, details=details
    )


def detail(kind: DetailKind, value: str, evidence: str) -> DraftDetail:
    return DraftDetail(kind=kind, value=value, evidence=evidence)


def test_a_plain_true_summary_is_kept() -> None:
    assert problems_with(draft(), request_for()) == ()


class TestQuality:
    def test_an_ending_other_than_the_facts_is_refused(self) -> None:
        assert problems_with(
            draft("Swift Parcels called, and you did not answer.", outcome=CallOutcome.FAILED),
            request_for(),
        ) == (DraftProblem.WRONG_OUTCOME, DraftProblem.OUTCOME_NOT_NAMED)

    def test_a_headline_with_no_words_is_refused(self) -> None:
        assert DraftProblem.EMPTY_HEADLINE in problems_with(draft(" ... "), request_for())

    def test_a_headline_past_the_summary_length_is_refused(self) -> None:
        long = "Your assistant noted where " + "the parcel went, " * 20
        assert len(long) > MAX_HEADLINE_CHARACTERS
        assert DraftProblem.TOO_LONG in problems_with(draft(long), request_for())

    def test_three_sentences_are_refused(self) -> None:
        three = "Swift Parcels called. The parcel is next door. Your assistant noted it."
        assert problems_with(draft(three), request_for()) == (DraftProblem.TOO_MANY_SENTENCES,)

    @pytest.mark.parametrize(
        "headline",
        [
            "Swift Parcels called. Your assistant noted it",
            "Swift Parcels comes back at 2 p.m. on Friday, which your assistant noted!",
            "Swift Parcels called? Your assistant noted it.",
        ],
    )
    def test_one_or_two_sentences_are_kept(self, headline: str) -> None:
        said = caller_said("It's Swift Parcels, back at 2 on Friday.")
        assert DraftProblem.TOO_MANY_SENTENCES not in problems_with(
            draft(headline), request_for(said)
        )

    @pytest.mark.parametrize(
        ("ending", "outcome", "headline"),
        [
            (
                Ending.RESOLVED,
                CallOutcome.RESOLVED_BY_AGENT,
                "Swift Parcels called about a parcel.",
            ),
            (Ending.HANDED_OVER, CallOutcome.HANDED_TO_USER, "Swift Parcels called; you answered."),
            (Ending.UNANSWERED, CallOutcome.UNANSWERED_ESCALATION, "Swift Parcels called."),
            (Ending.CALLER_HUNG_UP, CallOutcome.CALLER_HUNG_UP, "Swift Parcels hung on."),
            (Ending.FAILED, CallOutcome.FAILED, "Swift Parcels called and your assistant heard."),
        ],
    )
    def test_a_headline_that_does_not_say_how_it_ended_is_refused(
        self, ending: Ending, outcome: CallOutcome, headline: str
    ) -> None:
        assert problems_with(draft(headline, outcome=outcome), request_for(ending=ending)) == (
            DraftProblem.OUTCOME_NOT_NAMED,
        )

    @pytest.mark.parametrize(
        ("ending", "outcome", "headline"),
        [
            (
                Ending.HANDED_OVER,
                CallOutcome.HANDED_TO_USER,
                "Your assistant handed Swift Parcels to you.",
            ),
            (
                Ending.UNANSWERED,
                CallOutcome.UNANSWERED_ESCALATION,
                "Swift Parcels called, but you didn\u2019t answer.",
            ),
            (
                Ending.CALLER_HUNG_UP,
                CallOutcome.CALLER_HUNG_UP,
                "Swift Parcels Hung Up before saying more.",
            ),
            (Ending.FAILED, CallOutcome.FAILED, "Swift Parcels called, but handling it failed."),
        ],
    )
    def test_the_ending_is_recognised_whatever_the_case_or_apostrophe(
        self, ending: Ending, outcome: CallOutcome, headline: str
    ) -> None:
        assert problems_with(draft(headline, outcome=outcome), request_for(ending=ending)) == ()

    @pytest.mark.parametrize(
        "headline",
        [
            "The caller said Swift Parcels left a parcel next door; your assistant noted it.",
            "In summary, your assistant took a call from Swift Parcels.",
            "During the call, Swift Parcels told your assistant about a parcel.",
            "Um, Swift Parcels called and your assistant noted it.",
        ],
    )
    def test_filler_is_refused(self, headline: str) -> None:
        assert problems_with(draft(headline), request_for()) == (DraftProblem.FILLER,)

    def test_a_word_containing_filler_is_not_filler(self) -> None:
        assert (
            problems_with(
                draft("Swift Parcels umpired nothing; your assistant noted it."), request_for()
            )
            == ()
        )

    def test_a_stretch_of_the_call_copied_out_is_refused(self) -> None:
        copied = "Your assistant heard: I've left the parcel with the neighbour at 14 Alder Close."
        assert problems_with(draft(copied), request_for()) == (DraftProblem.RESTATES_THE_CALL,)

    def test_a_short_phrase_the_call_used_is_not_restatement(self) -> None:
        kept = (
            "Swift Parcels left it with the neighbour at 14 Alder Close; your assistant noted it."
        )
        assert problems_with(draft(kept), request_for()) == ()

    def test_a_number_nobody_said_is_refused(self) -> None:
        invented = "Swift Parcels left your parcel at 16 Alder Close, and your assistant noted it."
        assert problems_with(draft(invented), request_for()) == (DraftProblem.INVENTED_NUMBER,)

    def test_more_details_than_a_summary_keeps_are_refused(self) -> None:
        name = detail(DetailKind.NAME, "Swift Parcels", "it's Swift Parcels")
        assert problems_with(draft(FINE, *[name] * (MAX_DETAILS + 1)), request_for()) == (
            DraftProblem.TOO_MANY_DETAILS,
        )


class TestGrounding:
    @pytest.mark.parametrize(
        ("kind", "value", "evidence"),
        [
            (DetailKind.TIME, "Friday after two", "come back on Friday after two"),
            (DetailKind.NAME, "Swift Parcels", "Hi, it's Swift Parcels."),
            (DetailKind.REFERENCE_NUMBER, "SP 58213", "The tracking reference is SP 58213"),
            (DetailKind.ADDRESS, "14 Alder Close", "the neighbour at 14 Alder Close"),
            (DetailKind.AMOUNT, "the parcel", "I've left the parcel"),
            (DetailKind.COMMITMENT_MADE, "will come back", "the driver will come back"),
            (
                DetailKind.COMMITMENT_DECLINED,
                "left the parcel",
                "left the parcel with the neighbour",
            ),
        ],
    )
    def test_a_detail_of_each_kind_quoting_the_call_is_kept(
        self, kind: DetailKind, value: str, evidence: str
    ) -> None:
        assert problems_with(draft(FINE, detail(kind, value, evidence)), request_for()) == ()

    def test_evidence_differing_only_in_case_and_punctuation_is_kept(self) -> None:
        loose = detail(DetailKind.REFERENCE_NUMBER, "sp-58213", "tracking reference is: SP 58213")
        assert problems_with(draft(FINE, loose), request_for()) == ()

    @pytest.mark.parametrize(
        ("value", "evidence"),
        [
            pytest.param(
                "SP 58214", "The tracking reference is SP 58213", id="value not in evidence"
            ),
            pytest.param("SP 58213", "Your reference is SP 58213", id="evidence nobody said"),
            pytest.param(
                "SP 58213",
                "14 Alder Close. The tracking reference is SP 58213",
                id="across two lines",
            ),
            pytest.param("SP 58213", "SP 58213 tracking reference", id="words out of order"),
            pytest.param("...", "The tracking reference is SP 58213", id="a value with no words"),
            pytest.param("SP", "...", id="evidence with no words"),
        ],
    )
    def test_an_invented_detail_refuses_the_whole_draft(self, value: str, evidence: str) -> None:
        invented = detail(DetailKind.REFERENCE_NUMBER, value, evidence)
        real = detail(DetailKind.NAME, "Swift Parcels", "it's Swift Parcels")
        assert problems_with(draft(FINE, real, invented), request_for()) == (
            DraftProblem.UNGROUNDED_DETAIL,
        )

    def test_a_reference_promised_but_never_read_out_cannot_be_kept(self) -> None:
        said = caller_said("I don't have the reference in front of me, but we'll email it.")
        guessed = detail(DetailKind.REFERENCE_NUMBER, "reference", "the reference")
        numbered = detail(DetailKind.REFERENCE_NUMBER, "NE 1001", "the reference")
        assert problems_with(draft(FINE, guessed), request_for(said)) == ()
        assert problems_with(draft(FINE, numbered), request_for(said)) == (
            DraftProblem.UNGROUNDED_DETAIL,
        )

    def test_what_the_assistant_or_the_user_said_is_evidence_too(self) -> None:
        said = ((Speaker.AGENT, "I'll pass that on."), (Speaker.HUMAN, "I'm on my way."))
        promise = detail(DetailKind.COMMITMENT_MADE, "on my way", "I'm on my way")
        assert problems_with(draft(FINE, promise), request_for(said)) == ()


# A courier's call in Hindi, written for these tests. Devanagari puts vowel signs and the virama
# after the consonant they belong to, as marks rather than letters.
HINDI_COURIER = caller_said(
    "नमस्ते, मैं स्विफ्ट पार्सल से बोल रहा हूँ। आपका पार्सल पड़ोसी के घर चौदह नंबर पर छोड़ दिया है।",
    "ड्राइवर शुक्रवार को दो बजे के बाद फिर आएगा।",
)


class TestDevanagari:
    def test_a_word_keeps_its_vowel_signs_and_virama(self) -> None:
        assert words("ड्राइवर शुक्रवार को, फिर आएगा।") == ("ड्राइवर", "शुक्रवार", "को", "फिर", "आएगा")

    def test_a_detail_quoting_a_hindi_line_is_kept(self) -> None:
        when = detail(DetailKind.TIME, "शुक्रवार को दो बजे के बाद", "शुक्रवार को दो बजे के बाद फिर आएगा")
        found = problems_with(draft(FINE, when), request_for(HINDI_COURIER))
        assert DraftProblem.UNGROUNDED_DETAIL not in found

    def test_a_hindi_value_with_a_changed_vowel_sign_is_refused(self) -> None:
        # "आयेगा" for "आएगा": one sign apart, and a word nobody said.
        changed = detail(DetailKind.COMMITMENT_MADE, "फिर आयेगा", "फिर आएगा")
        found = problems_with(draft(FINE, changed), request_for(HINDI_COURIER))
        assert DraftProblem.UNGROUNDED_DETAIL in found

    def test_a_hindi_line_copied_out_is_restatement(self) -> None:
        copied = "आपका पार्सल पड़ोसी के घर चौदह नंबर पर छोड़ दिया है, your assistant noted it."
        found = problems_with(draft(copied), request_for(HINDI_COURIER))
        assert DraftProblem.RESTATES_THE_CALL in found

    def test_sentences_end_at_a_danda_and_at_a_stop_before_devanagari(self) -> None:
        three = "स्विफ्ट पार्सल ने फ़ोन किया। पार्सल पड़ोसी के घर है? सहायक ने नोट किया।"
        two = "स्विफ्ट पार्सल ने फ़ोन किया। your assistant ने नोट किया।"
        assert DraftProblem.TOO_MANY_SENTENCES in problems_with(
            draft(three), request_for(HINDI_COURIER)
        )
        assert DraftProblem.TOO_MANY_SENTENCES not in problems_with(
            draft(two), request_for(HINDI_COURIER)
        )


class TestVocabulary:
    @pytest.mark.parametrize("ending", list(Ending))
    def test_every_fallback_headline_names_its_own_ending(self, ending: Ending) -> None:
        # The fallback and the checks describe endings in the same words, or a fallback would be a
        # summary the product itself refuses.
        known = fallback_summary(ended(COURIER, ending), locale="en")
        assert names_the_ending(known.headline, known.outcome, vocabulary_for("en"))

    def test_every_outcome_can_be_named_in_every_locale(self) -> None:
        for vocabulary in SUMMARY_VOCABULARIES.values():
            assert set(vocabulary.endings) == set(CallOutcome)

    def test_a_vocabulary_that_cannot_name_an_ending_stops_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        english = SUMMARY_VOCABULARIES["en"]
        endings = {**english.endings, CallOutcome.FAILED: ()}
        monkeypatch.setattr(
            summary_checks,
            "SUMMARY_VOCABULARIES",
            {"en": SummaryVocabulary(endings=endings, filler=english.filler)},
        )
        with pytest.raises(InvariantError, match="cannot name the ending failed"):
            _every_vocabulary_is_complete()

    def test_a_regional_locale_reads_with_its_language(self) -> None:
        assert vocabulary_for("en-GB") is SUMMARY_VOCABULARIES["en"]
