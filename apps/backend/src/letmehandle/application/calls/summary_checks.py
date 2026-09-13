"""What a draft summary must be before it replaces the fallback, checked in code.

A prompt asks a model for a short summary that says how the call ended and invents nothing. These
checks are what make that true of every summary kept, because a model asked is a model that
usually complies, and the summary is the one record a user has once the transcript is gone.

Two kinds of check. Quality: one or two sentences, within the summary's length, naming how the call
ended in words the user would recognise, with no filler and no stretch of the conversation copied
out. Grounding: every detail quotes words that were actually said, its value is made only of words
from that quote, and no number appears in the headline that nobody said. A reference number that
was never read out is worse than none, so a draft carrying one is refused whole rather than trimmed:
a model that invented one detail is not a model whose other sentences can be trusted.

Words are compared case-folded and split on anything that is not a letter or a digit, so a draft is
not refused for a curly apostrophe or a capital letter, and is refused for a changed word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.tools.outcome import MAX_DETAILS
from letmehandle.application.preferences.context import DEFAULT_LOCALE, closest_phrasebook
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from letmehandle.application.calls.summariser import SummaryDraft, SummaryRequest

# A summary is what a person would say about a call, and nobody says three sentences of it.
MAX_SENTENCES: Final = 2

# A run of this many words in a row, found in one thing somebody said, is quoting rather than
# summarising. Short enough to catch a copied sentence, long enough to allow "your check-up on
# Thursday at ten", which a good headline repeats.
RESTATEMENT_WORDS: Final = 9

# A sentence ends at a stop followed by a capital, or at the end. "10 a.m. on Thursday" is one.
_SENTENCE_END: Final = re.compile(r"[.!?]+(?:\s+(?=[A-Z])|\s*$)")
_WORD: Final = re.compile(r"\w+")


class DraftProblem(StrEnum):
    """Why a draft was refused. Logged, so a prompt that degrades shows how."""

    WRONG_OUTCOME = "wrong_outcome"
    EMPTY_HEADLINE = "empty_headline"
    TOO_LONG = "too_long"
    TOO_MANY_SENTENCES = "too_many_sentences"
    OUTCOME_NOT_NAMED = "outcome_not_named"
    FILLER = "filler"
    RESTATES_THE_CALL = "restates_the_call"
    INVENTED_NUMBER = "invented_number"
    TOO_MANY_DETAILS = "too_many_details"
    UNGROUNDED_DETAIL = "ungrounded_detail"


@dataclass(frozen=True, slots=True)
class SummaryVocabulary:
    """The words the checks read a headline with, for one locale.

    `endings` lists, per outcome, phrases of which a headline must contain one; the model is shown
    the same list, so what it is asked to write and what is accepted cannot drift apart. `filler`
    lists phrases that narrate the call or the act of summarising it instead of saying what
    happened.
    """

    endings: Mapping[CallOutcome, tuple[str, ...]]
    filler: tuple[str, ...]


_ENGLISH: Final = SummaryVocabulary(
    endings={
        CallOutcome.RESOLVED_BY_AGENT: ("your assistant",),
        CallOutcome.HANDED_TO_USER: ("handed", "you joined", "you took over", "put through to you"),
        CallOutcome.PASSED_THROUGH: ("put through", "straight through"),
        CallOutcome.REJECTED_BY_RULE: ("your rules", "blocked"),
        CallOutcome.CALLER_HUNG_UP: ("hung up", "rang off"),
        CallOutcome.UNANSWERED_ESCALATION: (
            "did not answer",
            "didn't answer",
            "could not reach you",
            "couldn't reach you",
            "missed",
        ),
        CallOutcome.FAILED: ("failed", "went wrong"),
    },
    filler=(
        "the caller said",
        "the caller stated",
        "the caller mentioned",
        "during the call",
        "in this call",
        "on this call",
        "the call was about",
        "this call was about",
        "the transcript",
        "in summary",
        "to summarise",
        "to summarize",
        "as an ai",
        "i hope this helps",
        "worth noting",
        "basically",
        "um",
        "uh",
    ),
)

SUMMARY_VOCABULARIES: Final[Mapping[str, SummaryVocabulary]] = {DEFAULT_LOCALE: _ENGLISH}


def _every_vocabulary_is_complete() -> None:
    """Fail at import if a locale cannot name some ending, as the phrasebooks do."""
    for locale, vocabulary in SUMMARY_VOCABULARIES.items():
        unnamed = sorted(o.value for o in CallOutcome if not vocabulary.endings.get(o))
        if unnamed:
            raise InvariantError(
                f"the {locale} summary vocabulary cannot name the ending {', '.join(unnamed)}"
            )


_every_vocabulary_is_complete()


def vocabulary_for(locale: str) -> SummaryVocabulary:
    """The closest vocabulary to `locale`, narrowing as every phrasing does."""
    return closest_phrasebook(locale, SUMMARY_VOCABULARIES)


def words(text: str) -> tuple[str, ...]:
    """`text` as the checks compare it: case-folded words and numbers, in order."""
    return tuple(_WORD.findall(text.casefold()))


def names_the_ending(headline: str, outcome: CallOutcome, vocabulary: SummaryVocabulary) -> bool:
    """Whether `headline` says how the call ended in one of the vocabulary's phrases."""
    said = words(headline)
    return any(_contains_run(said, words(phrase)) for phrase in vocabulary.endings[outcome])


def problems_with(draft: SummaryDraft, request: SummaryRequest) -> tuple[DraftProblem, ...]:
    """Every reason to refuse `draft` as the summary of `request`. Empty means keep it."""
    vocabulary = vocabulary_for(request.locale)
    headline = words(draft.headline)
    said = [words(entry.text) for entry in request.transcript]
    everything_said = {word for entry in said for word in entry}
    checks = (
        (DraftProblem.WRONG_OUTCOME, draft.outcome is not request.known.outcome),
        (DraftProblem.EMPTY_HEADLINE, not headline),
        (DraftProblem.TOO_LONG, len(draft.headline) > MAX_HEADLINE_CHARACTERS),
        (DraftProblem.TOO_MANY_SENTENCES, _sentences(draft.headline) > MAX_SENTENCES),
        (
            DraftProblem.OUTCOME_NOT_NAMED,
            not names_the_ending(draft.headline, request.known.outcome, vocabulary),
        ),
        (
            DraftProblem.FILLER,
            any(_contains_run(headline, words(phrase)) for phrase in vocabulary.filler),
        ),
        (
            DraftProblem.RESTATES_THE_CALL,
            any(_longest_shared_run(headline, entry) >= RESTATEMENT_WORDS for entry in said),
        ),
        (
            DraftProblem.INVENTED_NUMBER,
            any(_is_number(word) and word not in everything_said for word in headline),
        ),
        (DraftProblem.TOO_MANY_DETAILS, len(draft.details) > MAX_DETAILS),
        (
            DraftProblem.UNGROUNDED_DETAIL,
            not all(_grounded(each.value, each.evidence, said) for each in draft.details),
        ),
    )
    return tuple(problem for problem, found in checks if found)


def _sentences(text: str) -> int:
    return len([part for part in _SENTENCE_END.split(text.strip()) if part.strip()])


def _is_number(word: str) -> bool:
    return any(character.isdigit() for character in word)


def _grounded(value: str, evidence: str, said: Sequence[tuple[str, ...]]) -> bool:
    """Whether `evidence` is words said in one utterance, and `value` only words from it."""
    quoted = words(evidence)
    stated = words(value)
    return (
        bool(quoted)
        and bool(stated)
        and set(stated) <= set(quoted)
        and any(_contains_run(entry, quoted) for entry in said)
    )


def _contains_run(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """Whether `needle` appears in `haystack` as consecutive words."""
    width = len(needle)
    return width > 0 and any(
        tuple(haystack[start : start + width]) == tuple(needle)
        for start in range(len(haystack) - width + 1)
    )


def _longest_shared_run(first: Sequence[str], second: Sequence[str]) -> int:
    """The length of the longest run of consecutive words the two have in common."""
    longest = 0
    previous = [0] * (len(second) + 1)
    for word in first:
        current = [0] * (len(second) + 1)
        for index, other in enumerate(second, start=1):
            if word == other:
                current[index] = previous[index - 1] + 1
                longest = max(longest, current[index])
        previous = current
    return longest
