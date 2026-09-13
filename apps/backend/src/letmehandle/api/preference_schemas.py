"""What the preferences API accepts and returns.

Separate from the domain types, as everywhere in this layer: the wire format is a contract with
an app that ships independently and cannot be updated in step.

The rule a client author needs, stated once: **an absent section is left exactly as it was, and
a section sent with an empty value is cleared.** Sending a list of no contacts removes them all;
omitting the field entirely leaves them alone.

Explicit `null` is treated as absent, not as a clear. The two are indistinguishable once a
payload has been parsed, and the empty value is the clearer instruction — so there is one way to
clear a section rather than two that could disagree.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from letmehandle.api.schemas import Request, Response
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

# Payloads that travel in both directions inherit `Request`: they are validated on the way in,
# and returning the same shape means the app parses one type per section rather than two that
# drift apart.


class TimeWindowPayload(Request):
    """A daily window, in the user's own zone.

    The zone travels with the window rather than being applied later. A window compared in the
    wrong zone is off by hours, and the mistake shows up as calls handled at the wrong time of
    day rather than as anything that looks like a bug.
    """

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
        """Rejected here as well as in the domain, so the client learns which field was wrong.

        The domain raises for the same reason; this repeats the check rather than trusting it,
        because a domain error becomes a 500 and a field error becomes something the app can
        point at.
        """
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
    # What the assistant may volunteer about the user unprompted. Bounded here as well as in
    # the domain, so a client learns which field was too long rather than getting a refusal
    # with no field attached to it.
    disclosable_facts: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=20
    )


class PrivacyPayload(Request):
    """What is kept, and for how long."""

    # Whole days. Bounded here as well as in the domain, so a client learns which field was out
    # of range rather than getting a refusal with no field attached to it.
    #
    # Required, with no default. It is the section's only field, so a default would make an
    # empty section mean "reset my retention" — an update that deletes transcripts the user
    # chose to keep, sent by a client that only meant to send nothing.
    transcript_retention_days: Annotated[
        int,
        Field(
            ge=TRANSCRIPT_RETENTION_FLOOR_DAYS,
            le=TRANSCRIPT_RETENTION_CEILING_DAYS,
            strict=True,
        ),
    ]


class PrivacyResponse(Response):
    """What is kept, and for how long, as stored.

    Unbounded above, unlike the request: a deployment with a higher ceiling may have stored a
    longer retention, and it is reported as it is rather than refused or quietly lowered.
    """

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
    """A change to some of it.

    Every section is optional, and an omitted section is left exactly as it was. That is what
    lets one screen save one section without knowing or caring what the others hold.
    """

    # Shaped like a language tag rather than merely non-empty. This is what the agent speaks
    # and what a voice is chosen for; "!!!!!!" reaching either of those is a call nobody can
    # understand rather than an error anybody sees.
    locale: (
        Annotated[
            str, Field(min_length=2, max_length=16, pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")
        ]
        | None
    ) = None
    call_handling: CallHandlingPayload | None = None
    important_contacts: list[ImportantContactPayload] | None = None
    hours: HoursPayload | None = None
    authority: AuthorityPayload | None = None
    notifications: NotificationsPayload | None = None
    personality: PersonalityPayload | None = None
    privacy: PrivacyPayload | None = None


class OnboardingResponse(Response):
    """Where somebody is in the flow, and what is left.

    Every list holds only the steps this deployment asks, in the order they are asked.
    `call_forwarding` is among them only where the profile's `call_forwarding` names a number,
    and an answer recorded to it elsewhere is not listed.
    """

    completed: list[OnboardingStep]
    skipped: list[OnboardingStep]
    remaining: list[OnboardingStep]
    next_step: OnboardingStep | None
    is_complete: bool


class OnboardingUpdate(Request):
    step: OnboardingStep
    # Skipping is a deliberate act, so it is said rather than inferred from an empty body.
    skipped: bool = False
