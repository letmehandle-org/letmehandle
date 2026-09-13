"""Preferences and onboarding, over HTTP: wire payloads to domain values and back."""

from __future__ import annotations

from datetime import time

from fastapi import APIRouter, status

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
from letmehandle.api.dependencies import CurrentUser, Preferences
from letmehandle.api.errors import UNPROCESSABLE, ApiError, invalid_request
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
    """Set everything the request mentions, starting from the defaults."""
    changes = _to_changes(body)
    try:
        return _to_response(await service.replace_all(user.id, changes))
    except InvariantError as error:
        raise invalid_request(error) from error


@router.patch("/preferences", response_model=PreferencesResponse, summary="Change some of it")
async def update_preferences(
    body: PreferencesUpdate, user: CurrentUser, service: Preferences
) -> PreferencesResponse:
    """Change the sections that were sent and leave the rest exactly as they were (D-023)."""
    try:
        return _to_response(await service.apply(user.id, _to_changes(body)))
    except InvariantError as error:
        # Invariants on the whole set run when the service composes it.
        raise invalid_request(error) from error


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
    """Record a step as answered or skipped; a step this deployment does not ask is refused."""
    try:
        progress = await service.record_step(user.id, body.step, skipped=body.skipped)
    except StepNotAskedError as error:
        raise ApiError(UNPROCESSABLE, "step_not_asked", str(error)) from error
    except InvariantError as error:
        # Skipping a step that cannot be skipped.
        raise invalid_request(error) from error
    return _progress_response(progress)


# --------------------------------------------------------------------- mapping


def _to_changes(body: PreferencesUpdate) -> PreferenceChanges:
    """Turn a payload into domain values, refusing what the domain refuses as a 422."""
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
        raise invalid_request(error) from error


def _window(payload: TimeWindowPayload | None) -> TimeWindow | None:
    if payload is None:
        return None
    return TimeWindow(start=_time(payload.start), end=_time(payload.end), zone=payload.zone)


def _time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


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
            # Sorted, so identical sets answer identically.
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
