"""The one place an escalation becomes the words on a lock screen.

Provider-independent: a platform decides how many bytes it accepts and how it encodes them, and
this decides what to say and what to give up when there is not room for all of it. The push and
the context endpoint are both built here, so the app reads the same words either way.

What goes in is deliberately little — why, who as far as anyone knows, what is needed, what has
been established, and the call id. What never goes in: a transcript, a recording, a number. A
notification is carried by two companies' servers and left on a lock screen.

Trimming is deterministic and ordered by what the user can most afford to lose: what has been
established first, then what is needed, then who is calling, and the reason last. Each is cut to
the longest prefix that fits rather than dropped whole, so a notification that is slightly too
large loses a few words rather than a sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final

from letmehandle.application.preferences.context import normalise_locale
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.ports.notification import EscalationNotification

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from letmehandle.domain.models.escalation_context import EscalationContext

ELLIPSIS: Final = "…"

# What the app reads to know which screen a notification opens. The only kind there is.
NOTIFICATION_KIND: Final = "escalation"


@dataclass(frozen=True, slots=True)
class EscalationPhrasebook:
    """Every phrase an escalation notification can show, for one locale (D-017)."""

    why: Mapping[EscalationReason, str]
    unknown_caller: str
    needed_prefix: str
    established_prefix: str
    nothing_known_yet: str


_ENGLISH: Final = EscalationPhrasebook(
    why={
        EscalationReason.CALLER_ASKED_FOR_THE_USER: "The caller asked for you",
        EscalationReason.ACTION_NOT_AUTHORISED: "The caller wants something only you can allow",
        EscalationReason.DECISION_NEEDS_THE_USER: "There is a decision only you can make",
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT: "This call looks important",
        EscalationReason.CANNOT_UNDERSTAND_THE_CALLER: "The caller cannot be understood",
        EscalationReason.USER_RULE_REQUIRES_IT: "One of your rules sends this call to you",
    },
    unknown_caller="Unknown caller",
    needed_prefix="Needs from you:",
    established_prefix="So far:",
    nothing_known_yet="Nothing more is known yet.",
)

_HINDI: Final = EscalationPhrasebook(
    why={
        EscalationReason.CALLER_ASKED_FOR_THE_USER: "कॉल करने वाले ने आपसे बात करनी चाही",
        EscalationReason.ACTION_NOT_AUTHORISED: (
            "कॉल करने वाला कुछ ऐसा चाहता है जिसकी अनुमति सिर्फ़ आप दे सकते हैं"
        ),
        EscalationReason.DECISION_NEEDS_THE_USER: "एक फ़ैसला है जो सिर्फ़ आप ले सकते हैं",
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT: "यह कॉल ज़रूरी लगती है",
        EscalationReason.CANNOT_UNDERSTAND_THE_CALLER: "कॉल करने वाले की बात समझ नहीं आ रही",
        EscalationReason.USER_RULE_REQUIRES_IT: "आपका एक नियम इस कॉल को आप तक भेजता है",
    },
    unknown_caller="अनजान कॉलर",
    needed_prefix="आपसे चाहिए:",
    established_prefix="अब तक:",
    nothing_known_yet="अभी और कुछ पता नहीं है।",
)

DEFAULT_LOCALE: Final = "en"
PHRASEBOOKS: Final[Mapping[str, EscalationPhrasebook]] = {DEFAULT_LOCALE: _ENGLISH, "hi": _HINDI}


def _every_reason_has_words() -> None:
    """Fail at import rather than while a phone is ringing, for the reason with no phrasing."""
    for locale, book in PHRASEBOOKS.items():
        missing = set(EscalationReason) - set(book.why)
        if missing:
            raise InvariantError(
                f"the {locale} escalation phrasebook cannot explain {', '.join(sorted(missing))}"
            )


_every_reason_has_words()


def phrasebook_for(locale: str) -> EscalationPhrasebook:
    """The closest phrasing available, narrowing from the full locale to its language."""
    normalised = normalise_locale(locale)
    for key in (normalised, normalised.split("-", 1)[0]):
        book = PHRASEBOOKS.get(key)
        if book is not None:
            return book
    return PHRASEBOOKS[DEFAULT_LOCALE]


@dataclass(frozen=True, slots=True)
class _Words:
    """The four pieces of text, before they become a notification."""

    why: str
    caller: str
    needed: str | None
    established: str | None


def notification_for(
    context: EscalationContext,
    *,
    fits: Callable[[EscalationNotification], bool] | None = None,
    locale: str = DEFAULT_LOCALE,
) -> EscalationNotification:
    """The notification for this escalation, trimmed until `fits` accepts it.

    Without `fits` nothing is trimmed: that is the context as the app shows it when it fetches
    rather than receives it. When nothing fits even at its shortest, the shortest is returned
    rather than an error — a notification the platform then refuses is a recorded outcome, and
    an exception here would be one more thing between an escalation and the user.
    """
    book = phrasebook_for(locale)
    words = _Words(
        why=book.why[context.reason],
        caller=context.caller_label or book.unknown_caller,
        needed=context.needed,
        established=context.established,
    )

    def build(candidate: _Words) -> EscalationNotification:
        return _assemble(context, candidate, book)

    accept = fits or _anything
    if accept(build(words)):
        return build(words)

    # In the order the user can most afford to lose them. Details may vanish entirely; the
    # caller and the reason always keep at least a character, because the notification refuses
    # to exist without a title and a label reading "…" still says a label was cut.
    for field, may_vanish in (
        ("established", True),
        ("needed", True),
        ("caller", False),
        ("why", False),
    ):
        words = _trim_field(words, field, may_vanish=may_vanish, fits=lambda w: accept(build(w)))
        if accept(build(words)):
            break
    return build(words)


def _anything(_: EscalationNotification) -> bool:
    return True


def _trim_field(
    words: _Words, field: str, *, may_vanish: bool, fits: Callable[[_Words], bool]
) -> _Words:
    """The same words with one field cut to the longest prefix that fits.

    A binary search over the prefix length: the payload grows with the prefix, so the longest
    fitting one is found in a handful of measurements rather than one per character. When no
    prefix fits, the field is left at its shortest so the next field can be tried.
    """
    text: str | None = getattr(words, field)
    if text is None:
        return words
    shortest = 0 if may_vanish else 1

    def at(length: int) -> _Words:
        # Keyed by name, which the checker cannot follow; a required field is never given None
        # because its shortest length is one.
        changes: dict[str, Any] = {field: _prefix(text, length)}
        return replace(words, **changes)

    low, high = shortest, len(text) - 1
    best = at(shortest)
    while low <= high:
        middle = (low + high) // 2
        candidate = at(middle)
        if fits(candidate):
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def _prefix(text: str, length: int) -> str | None:
    """The first `length` characters, marked as cut, or nothing at all for a length of zero."""
    if length <= 0:
        return None
    cut = text[:length].rstrip()
    return f"{cut or text[:length]}{ELLIPSIS}"


def _assemble(
    context: EscalationContext, words: _Words, book: EscalationPhrasebook
) -> EscalationNotification:
    lines = [
        f"{prefix} {text}"
        for prefix, text in (
            (book.needed_prefix, words.needed),
            (book.established_prefix, words.established),
        )
        if text is not None
    ]
    return EscalationNotification(
        call_id=context.call_id,
        # A cut field is never None here: the caller and the reason keep at least a character.
        title=words.why,
        body="\n".join(lines) or book.nothing_known_yet,
        caller_label=words.caller,
        data={
            "kind": NOTIFICATION_KIND,
            "call_id": context.call_id.value,
            "reason": context.reason.value,
            "status": context.status.value,
        },
    )
