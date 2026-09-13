"""The one place preferences become model input: deterministic, time resolved, no numbers."""

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
    """Every phrase this module can put in front of a model, for one locale (D-017)."""

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

# The phrasing used when the user's locale has none; the user's locale still reaches the model.
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
    """Fail at import if a phrasebook or the refusals miss a phrase."""
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
    """One thing the assistant may or may not do, stated either way."""

    capability: Capability
    granted: bool
    description: str


@dataclass(frozen=True, slots=True)
class ContactStatement:
    """An important contact as a label and a posture, without the number."""

    label: str
    posture: HandlingPosture


@dataclass(frozen=True, slots=True)
class PreferenceContext:
    """What the model is told about the person it answers for, and the version it came from."""

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
    # Whether the assistant is working right now, resolved here (D-030).
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
    """One spelling for one locale: trimmed, lower case, hyphenated."""
    return locale.strip().lower().replace("_", "-")


def closest_phrasebook[Book](locale: str, books: Mapping[str, Book]) -> Book:
    """The closest phrasing in `books`, narrowing from the full locale to its language."""
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
    """Assemble everything the model may know, as a pure function of preferences and `now`."""
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
