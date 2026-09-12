"""Between a preference document and the domain's own types.

One module, both directions, so that the two halves cannot drift: a field added to the writer
and forgotten in the reader is a preference that saves and silently disappears.

The document is the wire between two versions of this application, not between this application
and a person. It is written by whatever is deployed and read by whatever is deployed next, so
reading is deliberately forgiving — an unknown key is ignored and a missing one takes the
domain's default — while writing is exact.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    CallRules,
    DisclosableFact,
    Formality,
    HandlingPosture,
    ImportantContact,
    NotificationPreferences,
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)


def preferences_to_document(preferences: UserPreferences) -> dict[str, Any]:
    """Everything, in a shape that survives a round trip.

    Sets are written sorted. A `frozenset` iterates in hash order, which differs between runs,
    and an unsorted list makes two identical preference sets produce different documents — which
    turns every save into a change and makes a diff between two versions unreadable.
    """
    return {
        "version": preferences.version,
        "locale": preferences.locale,
        "formality": preferences.formality.value,
        "verbosity": preferences.verbosity.value,
        "topics": sorted(topic.name for topic in preferences.topics),
        "disclosable_facts": sorted(fact.text for fact in preferences.disclosable_facts),
        "authority": sorted(capability.value for capability in preferences.authority.capabilities),
        "notifications": {
            "on_handled_call": preferences.notifications.on_handled_call,
            "on_blocked_call": preferences.notifications.on_blocked_call,
            "on_missed_escalation": preferences.notifications.on_missed_escalation,
            "daily_summary": preferences.notifications.daily_summary,
            "respect_quiet_hours": preferences.notifications.respect_quiet_hours,
        },
        "important_contacts": [
            {
                "number": contact.number.value,
                "label": contact.label,
                "posture": contact.posture.value,
            }
            for contact in preferences.important_contacts
        ],
        "rules": {
            "default_posture": preferences.rules.default_posture.value,
            "anonymous_posture": preferences.rules.anonymous_posture.value,
            "posture_by_category": {
                category.value: posture.value
                for category, posture in sorted(
                    preferences.rules.posture_by_category.items(), key=lambda item: item[0].value
                )
            },
            "blocked_categories": sorted(
                category.value for category in preferences.rules.blocked_categories
            ),
            "escalate_at_or_above": preferences.rules.escalate_at_or_above.value,
            "working_hours": _window_to_document(preferences.rules.working_hours),
            "quiet_hours": _window_to_document(preferences.rules.quiet_hours),
        },
    }


def document_to_preferences(document: dict[str, Any]) -> UserPreferences:
    """Back into the domain, with every value validated on the way.

    A document written by an older version is read with today's defaults for anything it does
    not mention. That is the point of the version field beside it: what a reader cannot supply
    from the document it supplies from the defaults, and a migration can tell which is which.
    """
    rules_document: dict[str, Any] = document.get("rules", {})

    return UserPreferences(
        version=int(document.get("version", PREFERENCES_VERSION)),
        locale=str(document.get("locale", "en")),
        formality=_enum(Formality, document.get("formality"), Formality.NEUTRAL),
        verbosity=_enum(Verbosity, document.get("verbosity"), Verbosity.NORMAL),
        topics=frozenset(Topic(name) for name in document.get("topics", [])),
        disclosable_facts=frozenset(
            DisclosableFact(text) for text in document.get("disclosable_facts", [])
        ),
        authority=AgentAuthority(
            frozenset(
                capability
                for value in document.get("authority", [])
                if (capability := _enum(Capability, value, None)) is not None
            )
        ),
        notifications=_notifications_from_document(document.get("notifications", {})),
        important_contacts=tuple(
            _contact_from_document(entry) for entry in document.get("important_contacts", [])
        ),
        rules=CallRules(
            default_posture=_enum(
                HandlingPosture,
                rules_document.get("default_posture"),
                HandlingPosture.HANDLE_WITH_AGENT,
            ),
            anonymous_posture=_enum(
                HandlingPosture,
                rules_document.get("anonymous_posture"),
                HandlingPosture.HANDLE_WITH_AGENT,
            ),
            posture_by_category={
                category: posture
                for raw_category, raw_posture in rules_document.get(
                    "posture_by_category", {}
                ).items()
                if (category := _enum(CallerCategory, raw_category, None)) is not None
                and (posture := _enum(HandlingPosture, raw_posture, None)) is not None
            },
            blocked_categories=frozenset(
                category
                for raw in rules_document.get("blocked_categories", [])
                if (category := _enum(CallerCategory, raw, None)) is not None
            ),
            escalate_at_or_above=_importance(rules_document.get("escalate_at_or_above")),
            working_hours=_window_from_document(rules_document.get("working_hours")),
            quiet_hours=_window_from_document(rules_document.get("quiet_hours")),
        ),
    )


def progress_to_document(progress: OnboardingProgress) -> dict[str, list[str]]:
    return {
        "completed": sorted(step.value for step in progress.completed),
        "skipped": sorted(step.value for step in progress.skipped),
    }


def document_to_progress(completed: list[str], skipped: list[str]) -> OnboardingProgress:
    """A step this version does not recognise is dropped.

    A step removed from the flow should not stop somebody signing in, and one added by a newer
    deployment means nothing here. Either way the worst outcome is being asked a question again.
    """
    return OnboardingProgress(
        completed=frozenset(
            step for raw in completed if (step := _enum(OnboardingStep, raw, None)) is not None
        ),
        skipped=frozenset(
            step for raw in skipped if (step := _enum(OnboardingStep, raw, None)) is not None
        ),
    )


def _contact_from_document(entry: dict[str, Any]) -> ImportantContact:
    """One stored contact, or the domain's own error rather than a bare `KeyError`.

    A missing key is corruption, and it should arrive as the failure everything else in this
    module raises — otherwise it escapes as a server fault with nothing naming the cause.
    """
    try:
        return ImportantContact(
            number=PhoneNumber(entry["number"]),
            label=entry["label"],
            posture=_enum(HandlingPosture, entry.get("posture"), HandlingPosture.PASS_THROUGH),
        )
    except KeyError as error:
        raise InvariantError(f"a stored contact is missing {error}") from error


def _window_to_document(window: TimeWindow | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {
        "start": window.start.strftime("%H:%M"),
        "end": window.end.strftime("%H:%M"),
        "zone": window.zone,
    }


def _window_from_document(document: dict[str, str] | None) -> TimeWindow | None:
    """No window at all is `None`; a window that is there and unreadable raises.

    `None` and `{}` are not the same thing. The first is a user who set no quiet hours; the
    second is a row that lost them, and treating it as the first is exactly the silent
    disappearance this module argues against everywhere else.
    """
    if document is None:
        return None
    try:
        return TimeWindow(
            start=_parse_time(document["start"]),
            end=_parse_time(document["end"]),
            zone=document["zone"],
        )
    except (KeyError, ValueError) as error:
        # Raised, not dropped. This is corruption rather than a value from a newer deployment,
        # and the two want opposite handling: an unknown enum member is safely ignored, while
        # quiet hours that silently disappear mean a phone ringing at three in the morning with
        # nothing anywhere to say why.
        raise InvariantError(f"stored hours could not be read: {error}") from error


def _parse_time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def _notifications_from_document(document: dict[str, Any]) -> NotificationPreferences:
    defaults = NotificationPreferences()
    return NotificationPreferences(
        on_handled_call=bool(document.get("on_handled_call", defaults.on_handled_call)),
        on_blocked_call=bool(document.get("on_blocked_call", defaults.on_blocked_call)),
        on_missed_escalation=bool(
            document.get("on_missed_escalation", defaults.on_missed_escalation)
        ),
        daily_summary=bool(document.get("daily_summary", defaults.daily_summary)),
        respect_quiet_hours=bool(document.get("respect_quiet_hours", defaults.respect_quiet_hours)),
    )


def _importance(raw: object) -> CallImportance:
    """The stored threshold, or the default when it is not one this version knows."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return CallImportance.NOTABLE
    try:
        return CallImportance(raw)
    except ValueError:
        return CallImportance.NOTABLE


def _enum[E](kind: type[E], raw: object, fallback: E | None) -> E:
    """Read a value this version understands, or fall back.

    A value written by a newer deployment means nothing here, and refusing to load somebody's
    settings because of one unrecognised string would lock them out of their own account over a
    field they never set. Where there is no sensible fallback the caller passes `None` and drops
    the entry instead.
    """
    try:
        return kind(raw)  # type: ignore[call-arg]
    except (ValueError, KeyError):
        return fallback  # type: ignore[return-value]
