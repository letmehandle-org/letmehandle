"""How the user wants calls handled.

This is configuration, not behaviour. Nothing here decides anything; it states what the user
asked for, and the rules engine and the agent read it. The separation is the point: the same
call must be able to produce different outcomes for two users without a line of code differing.

Phase 3 collects these through onboarding and persists them. Phase 1 defines what they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.intent import CallImportance

# At run time, not under TYPE_CHECKING: it is a default factory, not only an annotation.
from letmehandle.domain.models.voice import VoiceSelection

if TYPE_CHECKING:
    from datetime import datetime, time, tzinfo

    from letmehandle.domain.models.caller import CallerCategory
    from letmehandle.domain.models.phone_number import PhoneNumber

# The shape these preferences were written in.
#
# Stored beside them, so that a later change can migrate what is there rather than guess what
# an older row meant. Without it, adding a field leaves every existing row ambiguous: absent
# because the user declined, or absent because the field did not exist when they answered.
#
# 2 added the chosen voice. A document written at 1 has none, which reads as "has not chosen"
# rather than "chose nothing" — and the difference matters, because the first resolves to the
# provider's default and the second would mean silence.
#
# 3 is the privacy section's (transcript retention), written by the orchestration work.
#
# 4 replaced working hours and quiet hours with one window of hours the assistant answers in
# (D-030). A document written earlier is read as around the clock: neither older window meant
# "the assistant answers now", and the one that is closest to the user's intent at night is the
# assistant still answering rather than their phone ringing.
PREFERENCES_VERSION: Final = 4


class HandlingPosture(StrEnum):
    """What should happen to a call before anyone has spoken to it."""

    # S105 reads the name as a password. It is a call routing decision.
    PASS_THROUGH = "pass_through"  # noqa: S105
    HANDLE_WITH_AGENT = "handle_with_agent"
    REJECT = "reject"


class Formality(StrEnum):
    """How the assistant should sound."""

    WARM = "warm"
    NEUTRAL = "neutral"
    FORMAL = "formal"


class Verbosity(StrEnum):
    """How much the assistant says.

    Separate from formality because they vary independently: a warm assistant can be brief, and
    a formal one can go on. Collapsing them into one dial would make half the combinations
    people actually want unreachable.
    """

    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"


@dataclass(frozen=True, slots=True)
class Topic:
    """Something the user cares about, normalised.

    A value object rather than free text, because the agent reads these. Text that reaches a
    model unvalidated is text somebody can put instructions in, and a caller who learns what a
    user's topics are has a way to write them.

    Normalised so that "School Run", "school run" and " school run " are one topic rather than
    three, which is what stops a list nobody can maintain. Splitting on whitespace also means a
    newline cannot survive into a topic, so a multi-line value cannot be smuggled into something
    the model reads as a list.
    """

    name: str

    MAX_LENGTH: ClassVar[int] = 60

    def __post_init__(self) -> None:
        normalised = " ".join(self.name.split()).lower()
        if not normalised:
            raise InvariantError("a topic with nothing in it cannot be matched against anything")
        if len(normalised) > self.MAX_LENGTH:
            raise InvariantError(
                f"a topic is at most {self.MAX_LENGTH} characters; longer than that it is a "
                f"sentence, and a sentence in a list the agent reads is an instruction"
            )
        object.__setattr__(self, "name", normalised)

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class DisclosableFact:
    """Something the assistant is allowed to volunteer about the user.

    "Works from home on Tuesdays", say — the kind of thing that makes an assistant useful and
    that a user would not want said to just anybody.

    Validated for the same reason a topic is, and more urgently. This text is put in front of
    the model while an unknown caller is talking to it, so it is both a disclosure the user
    chose *and* a place somebody could try to write an instruction. Bounded in length and
    collapsed to a single line: an instruction needs room, and this does not give it any.

    Case is kept, unlike a topic. A topic is matched against, so it is normalised; a fact is
    read out, so "Tuesdays" should not become "tuesdays".
    """

    text: str

    MAX_LENGTH: ClassVar[int] = 120

    def __post_init__(self) -> None:
        collapsed = " ".join(self.text.split())
        if not collapsed:
            raise InvariantError("a fact with nothing in it discloses nothing")
        if len(collapsed) > self.MAX_LENGTH:
            raise InvariantError(
                f"a disclosable fact is at most {self.MAX_LENGTH} characters; longer than that "
                f"it is a paragraph, and a paragraph in front of the model is room for an "
                f"instruction somebody else wrote"
            )
        object.__setattr__(self, "text", collapsed)

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class ImportantContact:
    """Somebody whose calls are treated differently.

    The number is the identity, as everywhere else in this product. The label is what the
    assistant calls them to nobody: it is shown to the user, and it is never read out to a
    caller, because confirming who is in somebody's contacts is a disclosure they did not ask
    for.
    """

    number: PhoneNumber
    label: str
    posture: HandlingPosture = HandlingPosture.PASS_THROUGH

    MAX_LABEL: ClassVar[int] = 80

    def __post_init__(self) -> None:
        # Collapsed the way a topic is, and for the same reason: this label is put in front of
        # the model, so a value spanning several lines is room for something shaped like an
        # instruction. The label is the user's own text about their own contact, but on a phone
        # it usually comes from an address book they did not write either.
        collapsed = " ".join(self.label.split())
        if not collapsed:
            raise InvariantError("an important contact needs a label, or the list is numbers")
        if len(collapsed) > self.MAX_LABEL:
            raise InvariantError(f"a label is at most {self.MAX_LABEL} characters")
        object.__setattr__(self, "label", collapsed)

    def __str__(self) -> str:
        """The label alone. The number is personal data belonging to somebody else."""
        return self.label


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """What is worth interrupting somebody for.

    Escalation is not configurable. Being told that the assistant needs you, while it needs
    you, is the product — a user who turned it off would have a phone ringing with no idea why,
    which is worse than not having the feature.

    Everything else is off by default. A product that notifies about everything is one people
    silence, and a silenced product cannot reach them when it matters.
    """

    on_handled_call: bool = False
    on_blocked_call: bool = False
    on_missed_escalation: bool = True
    daily_summary: bool = False
    # Outside the assistant's hours calls ring the user directly, so a note about a call the
    # assistant handled can wait until the hours begin rather than arriving on top of them.
    respect_active_hours: bool = True

    @property
    def on_escalation(self) -> bool:
        """Always true. Kept as a property so callers can ask without special-casing it."""
        return True


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A daily window, in the user's own timezone.

    The timezone is part of the window rather than applied later. A window compared in the
    wrong zone is off by hours, and the mistake shows up as calls handled at the wrong time of
    day rather than as anything that looks like a bug.

    A window may wrap past midnight — seven in the evening to two in the morning is an ordinary
    evening shift — so containment is not a simple between.
    """

    start: time
    end: time
    zone: str

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise InvariantError(
                "a window that starts and ends at the same moment covers nothing; "
                "for a whole day, use midnight to one minute before it"
            )
        if any(moment.second or moment.microsecond for moment in (self.start, self.end)):
            # Stored to the minute, so anything finer is lost on the way out and different on
            # the way back. Worse than lossy: two ends that differ only in seconds come back
            # equal, which this very constructor refuses — so the row saves and can never be
            # read again, and that user's preferences return a server error for ever.
            raise InvariantError(
                "a window is set to the minute; seconds are not stored and would be lost"
            )
        try:
            ZoneInfo(self.zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise InvariantError(f"{self.zone!r} is not a timezone") from error

    @property
    def tzinfo(self) -> tzinfo:
        return ZoneInfo(self.zone)

    @property
    def wraps_midnight(self) -> bool:
        return self.end < self.start

    def contains(self, instant: datetime) -> bool:
        """Whether the instant falls inside the window, in the window's own zone.

        The instant must be timezone-aware. A naive one would be interpreted as whatever the
        machine's clock happens to be set to, which is how a service that works in one region
        misbehaves in another.
        """
        if instant.tzinfo is None:
            raise InvariantError(
                "a time window can only be compared against an instant that knows its own "
                "timezone; a naive one silently means whatever the server is set to"
            )
        local = instant.astimezone(self.tzinfo).time()
        if self.wraps_midnight:
            return local >= self.start or local < self.end
        return self.start <= local < self.end


@dataclass(frozen=True, slots=True)
class CallRules:
    """The deterministic part, evaluated before the assistant is involved.

    Deterministic on purpose. A known caller should never wait for a model to decide whether
    they may ring, and a rule a user set should never be overruled by a judgement.
    """

    default_posture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT
    posture_by_category: dict[CallerCategory, HandlingPosture] = field(default_factory=dict)
    blocked_categories: frozenset[CallerCategory] = field(default_factory=frozenset)
    anonymous_posture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT
    # When the assistant answers. Outside it, calls ring the user as if there were no assistant.
    # Nothing means always (D-030): a user who never set hours has an assistant that answers
    # around the clock, which is what the product promises by default.
    active_hours: TimeWindow | None = None
    escalate_at_or_above: CallImportance = CallImportance.NOTABLE

    def __post_init__(self) -> None:
        overlap = self.blocked_categories & set(self.posture_by_category)
        if overlap:
            raise InvariantError(
                f"{', '.join(sorted(overlap))} is both blocked and given a handling posture, "
                f"so the outcome would depend on which rule happened to be read first"
            )

    def posture_for(self, category: CallerCategory) -> HandlingPosture:
        """What to do with a call from this kind of caller, before anyone has spoken."""
        if category in self.blocked_categories:
            return HandlingPosture.REJECT
        return self.posture_by_category.get(category, self.default_posture)

    def is_active_at(self, instant: datetime) -> bool:
        """Whether this instant is inside the hours the user asked the assistant to answer in.

        No window is every instant. Outside the window the assistant answers nothing and calls
        ring the user; what was rejected is still rejected (D-030, D-031).
        """
        return self.active_hours is None or self.active_hours.contains(instant)


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Everything the assistant reads about how this person wants to be represented."""

    rules: CallRules = field(default_factory=CallRules)
    authority: AgentAuthority = field(default_factory=AgentAuthority.none)
    notifications: NotificationPreferences = field(default_factory=NotificationPreferences)
    # How the assistant sounds. Empty means nothing has been chosen, which resolves to the
    # provider's default rather than to silence — see `resolve_voice`.
    voice: VoiceSelection = field(default_factory=VoiceSelection)
    formality: Formality = Formality.NEUTRAL
    verbosity: Verbosity = Verbosity.NORMAL
    locale: str = "en"
    important_contacts: tuple[ImportantContact, ...] = ()
    topics: frozenset[Topic] = field(default_factory=frozenset)
    # What the assistant may say about the user unprompted. Empty by default: the safe answer
    # to "where are they?" is not a location.
    disclosable_facts: frozenset[DisclosableFact] = field(default_factory=frozenset)
    version: int = PREFERENCES_VERSION

    MAX_CONTACTS: ClassVar[int] = 200
    MAX_TOPICS: ClassVar[int] = 50
    MAX_FACTS: ClassVar[int] = 20

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise InvariantError("a locale is required; the agent's language is configuration")
        if self.version < 1:
            raise InvariantError("preferences are written in a version, and versions start at 1")
        if len(self.important_contacts) > self.MAX_CONTACTS:
            raise InvariantError(
                f"at most {self.MAX_CONTACTS} important contacts. Beyond that the list is an "
                f"address book, and everything in it stops being important"
            )
        if len(self.topics) > self.MAX_TOPICS:
            raise InvariantError(f"at most {self.MAX_TOPICS} topics")
        if len(self.disclosable_facts) > self.MAX_FACTS:
            raise InvariantError(
                f"at most {self.MAX_FACTS} disclosable facts. Every one of them is something a "
                f"stranger can be told, so the list being short is the point"
            )

        numbers = [contact.number for contact in self.important_contacts]
        duplicates = {number for number in numbers if numbers.count(number) > 1}
        if duplicates:
            raise InvariantError(
                "one number appears twice in the important contacts, so which rule applies "
                "would depend on which entry is read first"
            )

    def contact_for(self, number: PhoneNumber) -> ImportantContact | None:
        """The user's own entry for this number, if they have one."""
        return next(
            (contact for contact in self.important_contacts if contact.number == number), None
        )

    def cares_about(self, topic: str) -> bool:
        """Whether this is something the user asked to hear about."""
        return Topic(topic) in self.topics
