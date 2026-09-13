"""How the user wants calls handled, stated as configuration that the rules and the agent read."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.intent import CallImportance

# Imported at run time because it is a default factory, not only an annotation.
from letmehandle.domain.models.voice import VoiceSelection

if TYPE_CHECKING:
    from datetime import datetime, time, tzinfo

    from letmehandle.domain.models.caller import CallerCategory
    from letmehandle.domain.models.phone_number import PhoneNumber

# The shape these preferences are written in, stored beside them (D-022).
PREFERENCES_VERSION: Final = 5

# How many days what was said on a call is kept (D-014).
TRANSCRIPT_RETENTION_DEFAULT_DAYS: Final = 7
TRANSCRIPT_RETENTION_FLOOR_DAYS: Final = 1
# The most anybody can choose here; a longer stored retention is kept as stored.
TRANSCRIPT_RETENTION_CEILING_DAYS: Final = 90


def check_retention_choice(days: int) -> int:
    """A retention somebody is choosing now: between the floor and the ceiling, or refused."""
    if not TRANSCRIPT_RETENTION_FLOOR_DAYS <= days <= TRANSCRIPT_RETENTION_CEILING_DAYS:
        raise InvariantError(
            f"transcripts are kept for between {TRANSCRIPT_RETENTION_FLOOR_DAYS} and "
            f"{TRANSCRIPT_RETENTION_CEILING_DAYS} days"
        )
    return days


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
    """How much the assistant says, independently of how formally."""

    BRIEF = "brief"
    NORMAL = "normal"
    DETAILED = "detailed"


@dataclass(frozen=True, slots=True)
class Topic:
    """Something the user cares about, lower-cased and collapsed to one short line."""

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
    """Something the assistant may volunteer about the user, as one short line, case kept."""

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
    """Somebody whose calls are treated differently, with a label never read out to a caller."""

    number: PhoneNumber
    label: str
    posture: HandlingPosture = HandlingPosture.PASS_THROUGH

    MAX_LABEL: ClassVar[int] = 80

    def __post_init__(self) -> None:
        # Collapsed to one line, since the label is put in front of the model.
        collapsed = " ".join(self.label.split())
        if not collapsed:
            raise InvariantError("an important contact needs a label, or the list is numbers")
        if len(collapsed) > self.MAX_LABEL:
            raise InvariantError(f"a label is at most {self.MAX_LABEL} characters")
        object.__setattr__(self, "label", collapsed)

    def __str__(self) -> str:
        """The label alone, never the number."""
        return self.label


@dataclass(frozen=True, slots=True)
class NotificationPreferences:
    """Which optional notifications the user wants; being told of an escalation is not optional."""

    on_handled_call: bool = False
    on_blocked_call: bool = False
    on_missed_escalation: bool = True
    daily_summary: bool = False
    # Whether a note about a handled call waits until the assistant's hours begin.
    respect_active_hours: bool = True


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A daily window, to the minute, in its own timezone, which may wrap past midnight."""

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
            # Stored to the minute, so a finer end could come back equal to the start.
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
        """Whether a timezone-aware instant falls inside the window, read in the window's zone."""
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
    """The deterministic rules a call meets before the assistant is involved."""

    default_posture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT
    posture_by_category: dict[CallerCategory, HandlingPosture] = field(default_factory=dict)
    blocked_categories: frozenset[CallerCategory] = field(default_factory=frozenset)
    anonymous_posture: HandlingPosture = HandlingPosture.HANDLE_WITH_AGENT
    # When the assistant answers; none means around the clock (D-030).
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
        """Whether this instant is inside the hours the assistant answers in (D-030)."""
        return self.active_hours is None or self.active_hours.contains(instant)


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Everything the assistant reads about how this person wants to be represented."""

    rules: CallRules = field(default_factory=CallRules)
    authority: AgentAuthority = field(default_factory=AgentAuthority.none)
    notifications: NotificationPreferences = field(default_factory=NotificationPreferences)
    # An empty selection resolves to the provider's default voice.
    voice: VoiceSelection = field(default_factory=VoiceSelection)
    formality: Formality = Formality.NEUTRAL
    verbosity: Verbosity = Verbosity.NORMAL
    locale: str = "en"
    important_contacts: tuple[ImportantContact, ...] = ()
    topics: frozenset[Topic] = field(default_factory=frozenset)
    # What the assistant may say about the user unprompted.
    disclosable_facts: frozenset[DisclosableFact] = field(default_factory=frozenset)
    # Whole days, at least the floor; beyond the ceiling only as stored elsewhere.
    transcript_retention_days: int = TRANSCRIPT_RETENTION_DEFAULT_DAYS
    version: int = PREFERENCES_VERSION

    MAX_CONTACTS: ClassVar[int] = 200
    MAX_TOPICS: ClassVar[int] = 50
    MAX_FACTS: ClassVar[int] = 20

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise InvariantError("a locale is required; the agent's language is configuration")
        if self.version < 1:
            raise InvariantError("preferences are written in a version, and versions start at 1")
        if self.transcript_retention_days < TRANSCRIPT_RETENTION_FLOOR_DAYS:
            raise InvariantError(
                f"transcripts are kept for at least {TRANSCRIPT_RETENTION_FLOOR_DAYS} day"
            )
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

    @property
    def retention_exceeds_ceiling(self) -> bool:
        """Whether this set keeps transcripts longer than the ceiling, which nothing here purges."""
        return self.transcript_retention_days > TRANSCRIPT_RETENTION_CEILING_DAYS

    def contact_for(self, number: PhoneNumber) -> ImportantContact | None:
        """The user's own entry for this number, if they have one."""
        return next(
            (contact for contact in self.important_contacts if contact.number == number), None
        )
