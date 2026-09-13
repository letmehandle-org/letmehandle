"""The one place a user's preferences become input for the model.

Everything the agent is told about the person it represents comes through here, so that the
question "could a caller learn this?" has a single file to read rather than a prompt template
to audit. Nothing else assembles preference text.

Three properties are the whole point, and each of them is a bug that has a way of arriving
quietly:

  The output is deterministic. Sets are iterated in sorted order, never in hash order, because
  a context whose field order changes between processes makes two identical calls produce two
  different model responses and nothing in a log will say why.

  Time is resolved here, not there. A window handed to a model is a window the model has to
  reason about, at which point whether the user is asleep depends on how well it did arithmetic
  on a timezone. What crosses the boundary is a resolved answer.

  Numbers do not cross. An important contact contributes a label and a posture; their number
  stays behind. A number in model context is a number one crafted question away from being read
  out to whoever is on the line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import Capability
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    Formality,
    HandlingPosture,
    Verbosity,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from letmehandle.domain.models.caller import CallerCategory
    from letmehandle.domain.models.intent import CallImportance
    from letmehandle.domain.models.preferences import UserPreferences


@dataclass(frozen=True, slots=True)
class Phrasebook:
    """Every phrase this module can put in front of a model, for one locale.

    The phrases live in a structure keyed by locale rather than inline at the point of use, so
    that adding a language is a new entry rather than a hunt through the code for string
    literals. Only English exists today; the shape is what stops that from being permanent.
    """

    tone: Mapping[Formality, str]
    length: Mapping[Verbosity, str]
    capability: Mapping[Capability, str]


_ENGLISH: Final = Phrasebook(
    tone={
        Formality.WARM: "warm and personable",
        Formality.NEUTRAL: "plain and even",
        Formality.FORMAL: "formal and reserved",
    },
    length={
        Verbosity.BRIEF: "one sentence wherever one will do",
        Verbosity.NORMAL: "as much as the caller needs and no more",
        Verbosity.DETAILED: "full answers, with the detail spelled out",
    },
    capability={
        Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY: "say whether the user is free",
        Capability.SHARE_DELIVERY_INSTRUCTIONS: "tell a courier where to leave a parcel",
        Capability.CONFIRM_APPOINTMENTS: "confirm an appointment",
        Capability.RESCHEDULE_APPOINTMENTS: "move an appointment to another time",
        Capability.DECLINE_ON_THE_USERS_BEHALF: "decline something on the user's behalf",
        Capability.TAKE_A_MESSAGE: "take a message",
        Capability.SHARE_CONTACT_DETAILS: "pass on the user's contact details",
    },
)

# The locale whose phrasing is used when the user's own has none written yet. The user's locale
# still reaches the model unchanged: which language to speak and which phrasebook happened to
# be available are different questions, and answering the first with the second is how a French
# user gets answered in English.
DEFAULT_LOCALE: Final = "en"

PHRASEBOOKS: Final[Mapping[str, Phrasebook]] = {DEFAULT_LOCALE: _ENGLISH}

# The sentence a tool refusal records, per locale, for the user reading their call history.
REFUSALS: Final[Mapping[str, Mapping[Capability, str]]] = {
    DEFAULT_LOCALE: {
        Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY: (
            "the assistant is not authorised to say whether the user is free"
        ),
        Capability.SHARE_DELIVERY_INSTRUCTIONS: (
            "the assistant is not authorised to tell a courier where to leave a parcel"
        ),
        Capability.CONFIRM_APPOINTMENTS: (
            "the assistant is not authorised to confirm an appointment"
        ),
        Capability.RESCHEDULE_APPOINTMENTS: (
            "the assistant is not authorised to move an appointment to another time"
        ),
        Capability.DECLINE_ON_THE_USERS_BEHALF: (
            "the assistant is not authorised to decline something on the user's behalf"
        ),
        Capability.TAKE_A_MESSAGE: "the assistant is not authorised to take a message",
        Capability.SHARE_CONTACT_DETAILS: (
            "the assistant is not authorised to pass on the user's contact details"
        ),
    },
    "hi": {
        Capability.ANSWER_QUESTIONS_ABOUT_AVAILABILITY: (
            "असिस्टेंट को यह बताने की अनुमति नहीं है कि आप खाली हैं या नहीं"
        ),
        Capability.SHARE_DELIVERY_INSTRUCTIONS: (
            "असिस्टेंट को कूरियर को पार्सल छोड़ने की जगह बताने की अनुमति नहीं है"
        ),
        Capability.CONFIRM_APPOINTMENTS: "असिस्टेंट को अपॉइंटमेंट पक्का करने की अनुमति नहीं है",
        Capability.RESCHEDULE_APPOINTMENTS: "असिस्टेंट को अपॉइंटमेंट का समय बदलने की अनुमति नहीं है",
        Capability.DECLINE_ON_THE_USERS_BEHALF: (
            "असिस्टेंट को आपकी ओर से किसी बात के लिए मना करने की अनुमति नहीं है"
        ),
        Capability.TAKE_A_MESSAGE: "असिस्टेंट को संदेश लेने की अनुमति नहीं है",
        Capability.SHARE_CONTACT_DETAILS: "असिस्टेंट को आपकी संपर्क जानकारी देने की अनुमति नहीं है",
    },
}


def _every_phrasebook_is_complete() -> None:
    """Fail at import if a phrasebook is missing a phrase.

    Checked here rather than where the phrase is read. A lookup that raises at read time raises
    while somebody is on a call, and only for the user whose formality happens to be the
    missing one — so the gap could sit unnoticed until the worst moment. A process that will
    not start is a gap somebody finds immediately.

    This is what makes adding a language safe: a new entry that forgets a member cannot be
    deployed.
    """
    for locale, book in PHRASEBOOKS.items():
        for kind, phrases in (
            (Formality, book.tone),
            (Verbosity, book.length),
            (Capability, book.capability),
        ):
            missing = set(kind) - set(phrases)
            if missing:
                raise InvariantError(
                    f"the {locale} phrasebook has no phrasing for "
                    f"{', '.join(sorted(str(member) for member in missing))}; "
                    f"the assistant would have nothing to say about it"
                )
    for locale, refusals in REFUSALS.items():
        unphrased = set(Capability) - set(refusals)
        if unphrased:
            raise InvariantError(
                f"the {locale} refusals have no sentence for {', '.join(sorted(unphrased))}"
            )


_every_phrasebook_is_complete()


@dataclass(frozen=True, slots=True)
class CapabilityStatement:
    """One thing the assistant may or may not do, said either way.

    Refusals are listed as explicitly as permissions. A model told only what it may do infers
    the rest from silence, and silence is the input a caller gets to shape.
    """

    capability: Capability
    granted: bool
    description: str


@dataclass(frozen=True, slots=True)
class ContactStatement:
    """An important contact, with the personal data removed.

    A label and a posture are what the agent needs to behave differently towards somebody. The
    number is how the caller was recognised, which happened before the model was involved.
    """

    label: str
    posture: HandlingPosture


@dataclass(frozen=True, slots=True)
class PreferenceContext:
    """What the model is told about the person it is answering for.

    Carries the preferences version it was built from, so that a stored transcript can be read
    against the rules that actually applied rather than against today's. Without it, a change
    to this module silently rewrites the history of every call already made.

    Notification preferences are deliberately absent. They govern how the user is told about a
    call afterwards, which is nothing the assistant does while a caller is listening, and every
    field here is a field somebody may try to talk their way into.
    """

    preferences_version: int
    locale: str
    tone: str
    length: str
    formality: Formality
    verbosity: Verbosity
    default_posture: HandlingPosture
    anonymous_posture: HandlingPosture
    posture_by_category: tuple[tuple[CallerCategory, HandlingPosture], ...]
    blocked_categories: tuple[CallerCategory, ...]
    escalate_at_or_above: CallImportance
    # Whether the user asked the assistant to be working right now (D-030). Resolved here, so the
    # model is told an answer rather than handed a window to do timezone arithmetic on.
    in_active_hours: bool
    capabilities: tuple[CapabilityStatement, ...]
    important_contacts: tuple[ContactStatement, ...]
    topics: tuple[str, ...]
    disclosable_facts: tuple[str, ...]

    @property
    def is_current_version(self) -> bool:
        """Whether this was built from preferences in the shape the code expects today."""
        return self.preferences_version == PREFERENCES_VERSION

    @property
    def granted_capabilities(self) -> tuple[Capability, ...]:
        """Only what was granted, for the code that enforces rather than the text that asks."""
        return tuple(statement.capability for statement in self.capabilities if statement.granted)


def normalise_locale(locale: str) -> str:
    """One spelling for one locale.

    `en_GB`, `EN-gb` and ` en-GB ` are the same language to a speaker and three different
    strings to a dictionary, which would be three different contexts for one user.
    """
    return locale.strip().lower().replace("_", "-")


def closest_phrasebook[Book](locale: str, books: Mapping[str, Book]) -> Book:
    """The closest phrasing in `books`, narrowing from the full locale to its language.

    Generic because more than one kind of phrasing is written per locale, and every kind has to
    fall back the same way: two lookups that narrow differently would put one user's summary in
    one language and their assistant's instructions in another.
    """
    normalised = normalise_locale(locale)
    language = normalised.split("-", 1)[0]
    for key in (normalised, language):
        book = books.get(key)
        if book is not None:
            return book
    return books[DEFAULT_LOCALE]


def phrasebook_for(locale: str) -> Phrasebook:
    """The closest phrasing available, narrowing from the full locale to its language."""
    return closest_phrasebook(locale, PHRASEBOOKS)


def refusal_for(locale: str, capability: Capability) -> str:
    """The sentence recording that `capability` was not granted, in the closest locale written."""
    return closest_phrasebook(locale, REFUSALS)[capability]


def build_preference_context(preferences: UserPreferences, *, now: datetime) -> PreferenceContext:
    """Assemble everything the model may know, from preferences and one instant.

    `now` is passed rather than read from the clock so that a context is a pure function of its
    inputs: the same call replayed for a bug report has to produce the same context, and a
    context that consults the wall clock never does.
    """
    if now.tzinfo is None:
        raise InvariantError(
            "preference context is built against an instant that knows its own timezone; "
            "a naive one silently means whatever the server is set to, and hours resolved in "
            "the wrong zone are hours the user never asked for"
        )

    rules = preferences.rules
    phrasebook = phrasebook_for(preferences.locale)

    return PreferenceContext(
        preferences_version=preferences.version,
        locale=normalise_locale(preferences.locale),
        tone=phrasebook.tone[preferences.formality],
        length=phrasebook.length[preferences.verbosity],
        formality=preferences.formality,
        verbosity=preferences.verbosity,
        default_posture=rules.default_posture,
        anonymous_posture=rules.anonymous_posture,
        posture_by_category=tuple(sorted(rules.posture_by_category.items())),
        blocked_categories=tuple(sorted(rules.blocked_categories)),
        escalate_at_or_above=rules.escalate_at_or_above,
        in_active_hours=rules.is_active_at(now),
        capabilities=tuple(
            CapabilityStatement(
                capability=capability,
                granted=preferences.authority.allows(capability),
                description=phrasebook.capability[capability],
            )
            for capability in sorted(Capability)
        ),
        important_contacts=tuple(
            ContactStatement(label=contact.label, posture=contact.posture)
            for contact in preferences.important_contacts
        ),
        topics=tuple(sorted(topic.name for topic in preferences.topics)),
        disclosable_facts=tuple(sorted(fact.text for fact in preferences.disclosable_facts)),
    )
