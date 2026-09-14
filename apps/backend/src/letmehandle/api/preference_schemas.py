"""What the preferences API accepts and returns: absent or null leaves, empty clears (D-023)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from letmehandle.api.schemas import LocaleTag, Request, Response
from letmehandle.domain.models.authority import Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingStep
from letmehandle.domain.models.preferences import (
    TRANSCRIPT_RETENTION_CEILING_DAYS,
    TRANSCRIPT_RETENTION_FLOOR_DAYS,
    Formality,
    HandlingPosture,
    Verbosity,
)

# Payloads that travel both ways inherit `Request`, so the app parses one type per section.


class TimeWindowPayload(Request):
    """A daily window, with the zone it is read in."""

    start: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
    end: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
    zone: Annotated[str, Field(min_length=1, max_length=64)]


class ImportantContactPayload(Request):
    phone_number: Annotated[str, Field(min_length=5, max_length=20)]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    posture: HandlingPosture = HandlingPosture.PASS_THROUGH


class CallHandlingPayload(Request):
    """What happens to a call before anybody has spoken to it."""

    default_posture: HandlingPosture
    anonymous_posture: HandlingPosture
    posture_by_category: dict[CallerCategory, HandlingPosture] = Field(default_factory=dict)
    blocked_categories: list[CallerCategory] = Field(default_factory=list)
    escalate_at_or_above: CallImportance

    @model_validator(mode="after")
    def _a_category_has_one_rule(self) -> CallHandlingPayload:
        """A category is blocked or given a posture, never both; checked here to name the field."""
        overlap = set(self.blocked_categories) & set(self.posture_by_category)
        if overlap:
            raise ValueError(
                f"{', '.join(sorted(overlap))} is both blocked and given a handling posture"
            )
        return self


class HoursPayload(Request):
    """When the assistant answers; outside it, calls ring the user. `null` is always (D-030)."""

    active: TimeWindowPayload | None = None


class AuthorityPayload(Request):
    """What the assistant may do on somebody's behalf. Everything opt-in."""

    capabilities: list[Capability] = Field(default_factory=list)


class NotificationsPayload(Request):
    on_handled_call: bool = False
    on_blocked_call: bool = False
    on_missed_escalation: bool = True
    daily_summary: bool = False
    respect_active_hours: bool = True


class PersonalityPayload(Request):
    formality: Formality = Formality.NEUTRAL
    verbosity: Verbosity = Verbosity.NORMAL
    topics: list[Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=list)
    # What the assistant may volunteer about the user, bounded here to name the field.
    disclosable_facts: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=20
    )


class PrivacyPayload(Request):
    """What is kept, and for how long."""

    # Whole days, required so an empty section never resets retention.
    transcript_retention_days: Annotated[
        int,
        Field(
            ge=TRANSCRIPT_RETENTION_FLOOR_DAYS,
            le=TRANSCRIPT_RETENTION_CEILING_DAYS,
            strict=True,
        ),
    ]


class PrivacyResponse(Response):
    """What is kept, and for how long, as stored and unbounded above."""

    transcript_retention_days: int


class PreferencesResponse(Response):
    """Everything, as it stands."""

    version: int
    locale: str
    call_handling: CallHandlingPayload
    important_contacts: list[ImportantContactPayload]
    hours: HoursPayload
    authority: AuthorityPayload
    notifications: NotificationsPayload
    personality: PersonalityPayload
    privacy: PrivacyResponse


class PreferencesUpdate(Request):
    """A change to some of it; an omitted section is left exactly as it was."""

    locale: LocaleTag | None = None
    call_handling: CallHandlingPayload | None = None
    important_contacts: list[ImportantContactPayload] | None = None
    hours: HoursPayload | None = None
    authority: AuthorityPayload | None = None
    notifications: NotificationsPayload | None = None
    personality: PersonalityPayload | None = None
    privacy: PrivacyPayload | None = None


class OnboardingResponse(Response):
    """Where somebody is in the flow, listing only the steps this deployment asks, in order."""

    completed: list[OnboardingStep]
    skipped: list[OnboardingStep]
    remaining: list[OnboardingStep]
    next_step: OnboardingStep | None
    is_complete: bool


class OnboardingUpdate(Request):
    step: OnboardingStep
    skipped: bool = False
