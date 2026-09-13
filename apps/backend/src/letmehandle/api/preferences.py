"""Preferences and onboarding, over HTTP.

The layer that turns a wire payload into a domain object and back. It holds no rules of its
own: every constraint is the domain's, and this constructs domain types so that a value the
domain would refuse is refused here — with a field name attached, which is the one thing the
domain cannot give a client.
"""

from __future__ import annotations

from datetime import time

from fastapi import APIRouter, status

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
from letmehandle.api.dependencies import CurrentUser, Preferences
from letmehandle.api.errors import UNPROCESSABLE, ApiError
from letmehandle.api.preference_schemas import (
    AuthorityPayload,
    CallHandlingPayload,
    HoursPayload,
    ImportantContactPayload,
    NotificationsPayload,
    OnboardingResponse,
    OnboardingUpdate,
    PersonalityPayload,
    PreferencesResponse,
    PreferencesUpdate,
    PrivacyResponse,
    TimeWindowPayload,
)
from letmehandle.application.preferences.service import (
    CallHandling,
    Hours,
    PreferenceChanges,
)
from letmehandle.domain.errors import InvariantError, StepNotAskedError
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.onboarding import Onboarding
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    DisclosableFact,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
)

router = APIRouter(
    prefix="/v1", tags=["preferences"], route_class=limited_body_route(JSON_BODY_LIMIT_BYTES)
)


@router.get("/preferences", response_model=PreferencesResponse, summary="Read preferences")
async def read_preferences(user: CurrentUser, service: Preferences) -> PreferencesResponse:
    return _to_response(await service.get(user.id))


@router.put("/preferences", response_model=PreferencesResponse, summary="Replace preferences")
async def replace_preferences(
    body: PreferencesUpdate, user: CurrentUser, service: Preferences
) -> PreferencesResponse:
    """Set everything the request mentions, from the defaults.

    Distinct from the patch below: this starts from the defaults rather than from what is
    stored, so a section left out is reset rather than kept. That is what a client means when
    it says "replace".
    """
    changes = _to_changes(body)
    try:
        return _to_response(await service.replace_all(user.id, changes))
    except InvariantError as error:
        raise _refused(error) from error


@router.patch("/preferences", response_model=PreferencesResponse, summary="Change some of it")
async def update_preferences(
    body: PreferencesUpdate, user: CurrentUser, service: Preferences
) -> PreferencesResponse:
    """Change the sections that were sent and leave the rest exactly as they were.

    The ordinary case: one screen saves one section, and has no idea what the others hold.
    """
    try:
        return _to_response(await service.apply(user.id, _to_changes(body)))
    except InvariantError as error:
        # The invariants on the whole set — how many contacts, duplicate numbers, a blank
        # locale — run when the service composes it, which is outside `_to_changes`. Without
        # this they escape as a 500, and a duplicate phone number becomes a server fault.
        raise _refused(error) from error


@router.get("/onboarding", response_model=OnboardingResponse, summary="Where setup is")
async def read_onboarding(user: CurrentUser, service: Preferences) -> OnboardingResponse:
    return _progress_response(await service.progress(user.id))


@router.post(
    "/onboarding",
    response_model=OnboardingResponse,
    status_code=status.HTTP_200_OK,
    summary="Record a step",
)
async def record_onboarding_step(
    body: OnboardingUpdate, user: CurrentUser, service: Preferences
) -> OnboardingResponse:
    """Record a step as answered, or deliberately passed over.

    Held here rather than on the device, so that reinstalling or signing in elsewhere resumes
    where somebody was instead of asking them everything again.

    A step that cannot be skipped, sent as skipped, is a 422 `invalid_request`. A step this
    deployment does not ask — `call_forwarding` where nothing needs forwarding — is a 422
    `step_not_asked`, and nothing is recorded.
    """
    try:
        progress = await service.record_step(user.id, body.step, skipped=body.skipped)
    except StepNotAskedError as error:
        # A 422 like any other step this deployment has no screen for, removed or invented:
        # the request names something that does not exist here, rather than conflicting with
        # a state somebody could change by trying again.
        raise ApiError(UNPROCESSABLE, "step_not_asked", str(error)) from error
    except InvariantError as error:
        # The only way to reach this is skipping a step that has no safe default, which the
        # client should not have offered — so it is a request problem rather than a fault.
        raise ApiError(UNPROCESSABLE, "invalid_request", str(error)) from error
    return _progress_response(progress)


# --------------------------------------------------------------------- mapping


def _to_changes(body: PreferencesUpdate) -> PreferenceChanges:
    """Turn a payload into domain values, refusing anything the domain would refuse.

    Every failure here is a 422 naming the section, because a domain error escaping this
    function is a 500 — and "your hours are impossible" is not a server fault.

    Call handling and hours are carried separately rather than combined into a `CallRules`.
    Combining them here would mean filling the half that was not sent from the defaults, which
    resets it: a user who blocked spam callers and then set their hours from another screen
    would find the blocking gone.
    """
    try:
        return PreferenceChanges(
            locale=body.locale,
            call_handling=(
                None
                if body.call_handling is None
                else CallHandling(
                    default_posture=body.call_handling.default_posture,
                    anonymous_posture=body.call_handling.anonymous_posture,
                    posture_by_category=dict(body.call_handling.posture_by_category),
                    blocked_categories=frozenset(body.call_handling.blocked_categories),
                    escalate_at_or_above=body.call_handling.escalate_at_or_above,
                )
            ),
            hours=(None if body.hours is None else Hours(active=_window(body.hours.active))),
            authority=(
                None
                if body.authority is None
                else AgentAuthority(frozenset(body.authority.capabilities))
            ),
            notifications=(
                None
                if body.notifications is None
                else NotificationPreferences(
                    on_handled_call=body.notifications.on_handled_call,
                    on_blocked_call=body.notifications.on_blocked_call,
                    on_missed_escalation=body.notifications.on_missed_escalation,
                    daily_summary=body.notifications.daily_summary,
                    respect_active_hours=body.notifications.respect_active_hours,
                )
            ),
            formality=None if body.personality is None else body.personality.formality,
            verbosity=None if body.personality is None else body.personality.verbosity,
            topics=(
                None
                if body.personality is None
                else frozenset(Topic(name) for name in body.personality.topics)
            ),
            disclosable_facts=(
                None
                if body.personality is None
                else frozenset(DisclosableFact(text) for text in body.personality.disclosable_facts)
            ),
            important_contacts=(
                None
                if body.important_contacts is None
                else tuple(
                    ImportantContact(
                        number=PhoneNumber.parse(entry.phone_number),
                        label=entry.label,
                        posture=entry.posture,
                    )
                    for entry in body.important_contacts
                )
            ),
            transcript_retention_days=(
                None if body.privacy is None else body.privacy.transcript_retention_days
            ),
        )
    except InvariantError as error:
        raise _refused(error) from error


def _window(payload: TimeWindowPayload | None) -> TimeWindow | None:
    if payload is None:
        return None
    return TimeWindow(start=_time(payload.start), end=_time(payload.end), zone=payload.zone)


def _time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def _refused(error: InvariantError) -> ApiError:
    """A value the domain will not accept is the request's problem, not the server's."""
    return ApiError(UNPROCESSABLE, "invalid_request", str(error))


def _to_response(preferences: UserPreferences) -> PreferencesResponse:
    rules = preferences.rules
    return PreferencesResponse(
        version=preferences.version,
        locale=preferences.locale,
        call_handling=CallHandlingPayload(
            default_posture=rules.default_posture,
            anonymous_posture=rules.anonymous_posture,
            posture_by_category=dict(rules.posture_by_category),
            blocked_categories=sorted(rules.blocked_categories),
            escalate_at_or_above=rules.escalate_at_or_above,
        ),
        important_contacts=[
            ImportantContactPayload(
                phone_number=contact.number.value,
                label=contact.label,
                posture=contact.posture,
            )
            for contact in preferences.important_contacts
        ],
        hours=HoursPayload(active=_window_payload(rules.active_hours)),
        authority=AuthorityPayload(capabilities=sorted(preferences.authority.capabilities)),
        notifications=NotificationsPayload(
            on_handled_call=preferences.notifications.on_handled_call,
            on_blocked_call=preferences.notifications.on_blocked_call,
            on_missed_escalation=preferences.notifications.on_missed_escalation,
            daily_summary=preferences.notifications.daily_summary,
            respect_active_hours=preferences.notifications.respect_active_hours,
        ),
        personality=PersonalityPayload(
            formality=preferences.formality,
            verbosity=preferences.verbosity,
            # Sorted, so that two identical preference sets produce identical responses and a
            # client comparing them does not see a change that is not one.
            topics=sorted(topic.name for topic in preferences.topics),
            disclosable_facts=sorted(fact.text for fact in preferences.disclosable_facts),
        ),
        privacy=PrivacyResponse(transcript_retention_days=preferences.transcript_retention_days),
    )


def _window_payload(window: TimeWindow | None) -> TimeWindowPayload | None:
    if window is None:
        return None
    return TimeWindowPayload(
        start=window.start.strftime("%H:%M"),
        end=window.end.strftime("%H:%M"),
        zone=window.zone,
    )


def _progress_response(progress: Onboarding) -> OnboardingResponse:
    return OnboardingResponse(
        completed=list(progress.completed),
        skipped=list(progress.skipped),
        remaining=list(progress.remaining),
        next_step=progress.next_step,
        is_complete=progress.is_complete,
    )
