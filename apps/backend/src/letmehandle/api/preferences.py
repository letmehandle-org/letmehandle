"""Preferences and onboarding, over HTTP.

The layer that turns a wire payload into a domain object and back. It holds no rules of its
own: every constraint is the domain's, and this constructs domain types so that a value the
domain would refuse is refused here — with a field name attached, which is the one thing the
domain cannot give a client.
"""

from __future__ import annotations

from datetime import time

from fastapi import APIRouter, status

from letmehandle.api.dependencies import CurrentUser, Preferences
from letmehandle.api.errors import ApiError
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
    TimeWindowPayload,
)
from letmehandle.application.preferences.service import PreferenceChanges
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority
from letmehandle.domain.models.onboarding import ORDER, OnboardingProgress
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
)

router = APIRouter(prefix="/v1", tags=["preferences"])


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
    return _to_response(await service.replace_all(user.id, _apply_to(UserPreferences(), changes)))


@router.patch("/preferences", response_model=PreferencesResponse, summary="Change some of it")
async def update_preferences(
    body: PreferencesUpdate, user: CurrentUser, service: Preferences
) -> PreferencesResponse:
    """Change the sections that were sent and leave the rest exactly as they were.

    The ordinary case: one screen saves one section, and has no idea what the others hold.
    """
    return _to_response(await service.apply(user.id, _to_changes(body)))


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
    """
    try:
        progress = await service.record_step(user.id, body.step, skipped=body.skipped)
    except InvariantError as error:
        # The only way to reach this is skipping a step that has no safe default, which the
        # client should not have offered — so it is a request problem rather than a fault.
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_request", str(error)
        ) from error
    return _progress_response(progress)


# --------------------------------------------------------------------- mapping


def _to_changes(body: PreferencesUpdate) -> PreferenceChanges:
    """Turn a payload into domain values, refusing anything the domain would refuse.

    Every failure here is a `422` naming the section, because a domain error escaping this
    function is a 500 — and "your quiet hours are impossible" is not a server fault.
    """
    try:
        return PreferenceChanges(
            locale=body.locale,
            rules=_rules(body.call_handling, body.hours),
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
                    respect_quiet_hours=body.notifications.respect_quiet_hours,
                )
            ),
            formality=None if body.personality is None else body.personality.formality,
            verbosity=None if body.personality is None else body.personality.verbosity,
            topics=(
                None
                if body.personality is None
                else frozenset(Topic(name) for name in body.personality.topics)
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
        )
    except InvariantError as error:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_request", str(error)
        ) from error


def _rules(handling: CallHandlingPayload | None, hours: HoursPayload | None) -> CallRules | None:
    """Call handling and hours are one domain object, and two screens.

    Sending only one of them has to keep the other, which is why this cannot simply be built
    from whichever payload arrived. The service sees a whole `CallRules` or nothing, so the
    parts that were not sent are filled from the defaults here and corrected by the service
    against what is stored.
    """
    if handling is None and hours is None:
        return None

    defaults = CallRules()
    return CallRules(
        default_posture=(
            defaults.default_posture if handling is None else handling.default_posture
        ),
        anonymous_posture=(
            defaults.anonymous_posture if handling is None else handling.anonymous_posture
        ),
        posture_by_category=(
            dict(defaults.posture_by_category)
            if handling is None
            else dict(handling.posture_by_category)
        ),
        blocked_categories=(
            defaults.blocked_categories
            if handling is None
            else frozenset(handling.blocked_categories)
        ),
        escalate_at_or_above=(
            defaults.escalate_at_or_above if handling is None else handling.escalate_at_or_above
        ),
        working_hours=None if hours is None else _window(hours.working),
        quiet_hours=None if hours is None else _window(hours.quiet),
    )


def _window(payload: TimeWindowPayload | None) -> TimeWindow | None:
    if payload is None:
        return None
    return TimeWindow(start=_time(payload.start), end=_time(payload.end), zone=payload.zone)


def _time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def _apply_to(base: UserPreferences, changes: PreferenceChanges) -> UserPreferences:
    """Build a complete set from the defaults plus what was sent.

    Used only by the replace route. The patch route goes through the service, which starts
    from what is stored instead.
    """
    from dataclasses import replace as replace_fields

    return replace_fields(
        base,
        locale=base.locale if changes.locale is None else changes.locale,
        rules=base.rules if changes.rules is None else changes.rules,
        authority=base.authority if changes.authority is None else changes.authority,
        notifications=(
            base.notifications if changes.notifications is None else changes.notifications
        ),
        formality=base.formality if changes.formality is None else changes.formality,
        verbosity=base.verbosity if changes.verbosity is None else changes.verbosity,
        topics=base.topics if changes.topics is None else changes.topics,
        important_contacts=(
            base.important_contacts
            if changes.important_contacts is None
            else changes.important_contacts
        ),
    )


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
        hours=HoursPayload(
            working=_window_payload(rules.working_hours),
            quiet=_window_payload(rules.quiet_hours),
        ),
        authority=AuthorityPayload(capabilities=sorted(preferences.authority.capabilities)),
        notifications=NotificationsPayload(
            on_handled_call=preferences.notifications.on_handled_call,
            on_blocked_call=preferences.notifications.on_blocked_call,
            on_missed_escalation=preferences.notifications.on_missed_escalation,
            daily_summary=preferences.notifications.daily_summary,
            respect_quiet_hours=preferences.notifications.respect_quiet_hours,
        ),
        personality=PersonalityPayload(
            formality=preferences.formality,
            verbosity=preferences.verbosity,
            # Sorted, so that two identical preference sets produce identical responses and a
            # client comparing them does not see a change that is not one.
            topics=sorted(topic.name for topic in preferences.topics),
        ),
    )


def _window_payload(window: TimeWindow | None) -> TimeWindowPayload | None:
    if window is None:
        return None
    return TimeWindowPayload(
        start=window.start.strftime("%H:%M"),
        end=window.end.strftime("%H:%M"),
        zone=window.zone,
    )


def _progress_response(progress: OnboardingProgress) -> OnboardingResponse:
    return OnboardingResponse(
        completed=[step for step in ORDER if step in progress.completed],
        skipped=[step for step in ORDER if step in progress.skipped],
        remaining=list(progress.remaining),
        next_step=progress.next_step,
        is_complete=progress.is_complete,
    )
