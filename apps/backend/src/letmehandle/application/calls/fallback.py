"""The summary built from facts alone, for when a model's summary cannot be had.

When a call ends, orchestration asks a model for its summary. When that fails — the model is
down, times out, or returns something the domain refuses — this is what is written instead,
because a call with no summary is a call missing from the user's history, and the summary is the
only record left once the transcript is purged (D-014).

It says only what orchestration already knows for certain: how the call ended, who was on it,
whether the user was reached, and who the caller was taken to be. It extracts no details and
guesses no intent. A plain sentence that is true beats a rich one that might not be.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.application.calls.participation import human_joined_at
from letmehandle.application.preferences.context import DEFAULT_LOCALE, closest_phrasebook
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.call import CallHandling
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome, CallSummary

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.escalation import EscalationReason


@dataclass(frozen=True, slots=True)
class SummaryPhrasebook:
    """Every phrase a fallback headline can contain, for one locale.

    `headline` is a sentence per outcome with a `{caller}` slot; `caller` says who called by
    category, for anybody the user has not named; `withheld` is an unknown caller who hid their
    number.
    """

    headline: Mapping[CallOutcome, str]
    caller: Mapping[CallerCategory, str]
    withheld: str


_ENGLISH: Final = SummaryPhrasebook(
    headline={
        CallOutcome.RESOLVED_BY_AGENT: "Your assistant took a call from {caller}.",
        CallOutcome.HANDED_TO_USER: (
            "Your assistant took a call from {caller} and handed it over to you."
        ),
        CallOutcome.PASSED_THROUGH: "A call from {caller} was put straight through to you.",
        CallOutcome.REJECTED_BY_RULE: "A call from {caller} was ended by your rules.",
        CallOutcome.CALLER_HUNG_UP: "A call from {caller} ended when the caller hung up.",
        CallOutcome.UNANSWERED_ESCALATION: (
            "Your assistant tried to reach you about a call from {caller}, but you did not answer."
        ),
        CallOutcome.FAILED: "A call from {caller} could not be handled because something failed.",
    },
    caller={
        CallerCategory.KNOWN_CONTACT: "one of your contacts",
        CallerCategory.DELIVERY: "a delivery service",
        CallerCategory.HEALTHCARE: "a healthcare provider",
        CallerCategory.EDUCATION: "a school or college",
        CallerCategory.FINANCIAL: "a financial service",
        CallerCategory.SERVICE_PROVIDER: "a service provider",
        CallerCategory.SALES: "a sales caller",
        CallerCategory.SPAM: "a likely spam caller",
        CallerCategory.UNKNOWN: "an unknown caller",
    },
    withheld="a caller who withheld their number",
)

_HINDI: Final = SummaryPhrasebook(
    headline={
        CallOutcome.RESOLVED_BY_AGENT: "आपके सहायक ने {caller} की कॉल ली।",
        CallOutcome.HANDED_TO_USER: "आपके सहायक ने {caller} की कॉल ली और उसे आपको सौंप दिया।",
        CallOutcome.PASSED_THROUGH: "{caller} की कॉल सीधे आप तक पहुँचाई गई।",
        CallOutcome.REJECTED_BY_RULE: "{caller} की कॉल आपके नियमों से खत्म की गई।",
        CallOutcome.CALLER_HUNG_UP: "{caller} की कॉल तब खत्म हुई जब कॉल करने वाले ने फ़ोन रख दिया।",
        CallOutcome.UNANSWERED_ESCALATION: (
            "आपके सहायक ने {caller} की कॉल के बारे में आपसे संपर्क करने की कोशिश की, लेकिन आपने जवाब नहीं दिया।"
        ),
        CallOutcome.FAILED: "{caller} की कॉल कुछ गड़बड़ होने से संभाली नहीं जा सकी।",
    },
    caller={
        CallerCategory.KNOWN_CONTACT: "आपके एक संपर्क",
        CallerCategory.DELIVERY: "एक डिलीवरी सेवा",
        CallerCategory.HEALTHCARE: "एक स्वास्थ्य सेवा",
        CallerCategory.EDUCATION: "एक स्कूल या कॉलेज",
        CallerCategory.FINANCIAL: "एक वित्तीय सेवा",
        CallerCategory.SERVICE_PROVIDER: "एक सेवा प्रदाता",
        CallerCategory.SALES: "एक बिक्री करने वाले कॉलर",
        CallerCategory.SPAM: "एक संभावित स्पैम कॉलर",
        CallerCategory.UNKNOWN: "एक अनजान कॉलर",
    },
    withheld="नंबर छिपाने वाले एक कॉलर",
)

SUMMARY_PHRASEBOOKS: Final[Mapping[str, SummaryPhrasebook]] = {
    DEFAULT_LOCALE: _ENGLISH,
    "hi": _HINDI,
}


def _every_phrasebook_is_complete() -> None:
    """Fail at import if a phrasebook cannot describe some outcome or caller.

    For the reason the preference phrasebooks are checked the same way: a gap found at read time
    is found as a call ends, for one unlucky user, at exactly the moment the fallback exists for.
    """
    for locale, book in SUMMARY_PHRASEBOOKS.items():
        for kind, phrases in ((CallOutcome, book.headline), (CallerCategory, book.caller)):
            missing = set(kind) - set(phrases)
            if missing:
                raise InvariantError(
                    f"the {locale} summary phrasebook has no phrasing for "
                    f"{', '.join(sorted(str(member) for member in missing))}"
                )


_every_phrasebook_is_complete()


@dataclass(frozen=True, slots=True)
class CallFacts:
    """What orchestration knows about a call that has ended, without asking anybody.

    `escalation_reason` is set when the assistant asked for the user, whether or not they came.
    `caller_hung_up` is what the transport reported; it is not inferred. `intent` and
    `importance` are whatever classification the call reached before generation failed, and the
    honest defaults when it reached none.
    """

    call: CallSession
    escalation_reason: EscalationReason | None = None
    caller_hung_up: bool = False
    intent: CallIntent = CallIntent.UNDETERMINED
    importance: CallImportance = CallImportance.ROUTINE


def fallback_summary(facts: CallFacts, *, locale: str) -> CallSummary:
    """A valid summary of an ended call, from its facts alone.

    What Phase 8 writes when model generation fails. It never fails for a call that has ended,
    whatever state it ended in: the headline always fits, and the user is recorded as joining
    only when there was an escalation for them to join, as a summary requires. Raises
    `InvariantError` for a call still in progress, which has nothing to summarise yet.
    """
    call = facts.call
    if call.ended_at is None:
        raise InvariantError("only a call that has ended can be summarised")
    human_joined_at = _human_joined_at(facts, ended_at=call.ended_at)
    outcome = _outcome(facts, human_joined=human_joined_at is not None)
    return CallSummary(
        call_id=call.id,
        caller=call.caller,
        intent=facts.intent,
        importance=facts.importance,
        outcome=outcome,
        headline=_headline(outcome, call.caller, closest_phrasebook(locale, SUMMARY_PHRASEBOOKS)),
        started_at=call.started_at,
        ended_at=call.ended_at,
        human_joined_at=human_joined_at,
        escalation_reason=facts.escalation_reason,
    )


def _human_joined_at(facts: CallFacts, *, ended_at: datetime) -> datetime | None:
    # Only with the escalation that brought them: a summary refuses a join with no reason, and
    # the reason is what the history shows the user about why they were asked.
    joined = human_joined_at(facts.call)
    if facts.escalation_reason is None or joined is None:
        return None
    # Inside the call. A join is timed by this host and the start by the carrier, so a join a
    # few milliseconds early is skew rather than a join before the call, and a summary refuses
    # it; the call's own end is clamped to its start for the same reason.
    return min(max(joined, facts.call.started_at), ended_at)


def _outcome(facts: CallFacts, *, human_joined: bool) -> CallOutcome:
    """How the call ended, in order of what the user most needs to hear."""
    call = facts.call
    if call.state is CallState.REJECTED:
        return CallOutcome.REJECTED_BY_RULE
    if call.state is CallState.FAILED:
        return CallOutcome.FAILED
    if human_joined:
        return CallOutcome.HANDED_TO_USER
    # Read from whom routing gave the call to, not from who joined: a caller who hangs up before
    # the assistant's leg joins was never put through to anybody.
    if call.handling is not CallHandling.ASSISTANT:
        return CallOutcome.PASSED_THROUGH
    # Before a hang-up: the caller giving up while the user's phone rang is still a call the
    # user was wanted on and missed, and that is the part they can act on.
    if facts.escalation_reason is not None:
        return CallOutcome.UNANSWERED_ESCALATION
    if facts.caller_hung_up:
        return CallOutcome.CALLER_HUNG_UP
    return CallOutcome.RESOLVED_BY_AGENT


def _headline(outcome: CallOutcome, caller: Caller, book: SummaryPhrasebook) -> str:
    template = book.headline[outcome]
    by_category = book.withheld if _is_withheld_stranger(caller) else book.caller[caller.category]
    # A name only for somebody the user knows: a network can supply one for a stranger, and
    # repeating it would dress a guess up as an introduction.
    if caller.is_known and caller.display_name is not None:
        named = template.format(caller=caller.display_name)
        # A contact saved with a very long name gets the category rather than a cut-off name.
        if len(named) <= MAX_HEADLINE_CHARACTERS:
            return named
    return template.format(caller=by_category)


def _is_withheld_stranger(caller: Caller) -> bool:
    return caller.is_anonymous and caller.category is CallerCategory.UNKNOWN
