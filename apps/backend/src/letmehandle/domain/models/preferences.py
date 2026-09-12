"""How the user wants calls handled.

This is configuration, not behaviour. Nothing here decides anything; it states what the user
asked for, and the rules engine and the agent read it. The separation is the point: the same
call must be able to produce different outcomes for two users without a line of code differing.

Phase 3 collects these through onboarding and persists them. Phase 1 defines what they are.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.intent import CallImportance

if TYPE_CHECKING:
    from datetime import datetime, time, tzinfo

    from letmehandle.domain.models.caller import CallerCategory


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


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """A daily window, in the user's own timezone.

    The timezone is part of the window rather than applied later. A window compared in the
    wrong zone is off by hours, and the mistake shows up as calls handled at the wrong time of
    day rather than as anything that looks like a bug.

    A window may wrap past midnight — twenty-two hundred to seven is the ordinary case for
    quiet hours — so containment is not a simple between.
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
    quiet_hours: TimeWindow | None = None
    working_hours: TimeWindow | None = None
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

    def is_quiet_at(self, instant: datetime) -> bool:
        return self.quiet_hours is not None and self.quiet_hours.contains(instant)

    def is_working_at(self, instant: datetime) -> bool:
        """Whether the user is at work.

        Absent working hours means unknown rather than never: a user who has not said cannot be
        assumed to be unavailable, or the assistant would take every call.
        """
        return self.working_hours is None or self.working_hours.contains(instant)


@dataclass(frozen=True, slots=True)
class UserPreferences:
    """Everything the assistant reads about how this person wants to be represented."""

    rules: CallRules = field(default_factory=CallRules)
    authority: AgentAuthority = field(default_factory=AgentAuthority.none)
    formality: Formality = Formality.NEUTRAL
    locale: str = "en"
    # What the assistant may say about the user unprompted. Empty by default: the safe answer
    # to "where are they?" is not a location.
    disclosable_facts: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.locale.strip():
            raise InvariantError("a locale is required; the agent's language is configuration")
