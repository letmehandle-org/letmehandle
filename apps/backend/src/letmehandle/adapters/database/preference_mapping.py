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
from enum import Enum
from typing import Any

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import CallerCategory
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    TRANSCRIPT_RETENTION_DEFAULT_DAYS,
    TRANSCRIPT_RETENTION_FLOOR_DAYS,
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
from letmehandle.domain.models.voice import VoiceSelection


def preferences_to_document(preferences: UserPreferences) -> dict[str, Any]:
    """Everything, in a shape that survives a round trip.

    Sets are written sorted. A `frozenset` iterates in hash order, which differs between runs,
    and an unsorted list makes two identical preference sets produce different documents — which
    turns every save into a change and makes a diff between two versions unreadable.
    """
    return {
        # Today's version, not the one this set was read at. The field describes the shape
        # of the document being written, and a row that gains a version-2 field while still
        # labelled 1 is a row no future migration can reason about: it cannot tell a value
        # the user chose from one that did not exist when they answered.
        "version": PREFERENCES_VERSION,
        "locale": preferences.locale,
        "formality": preferences.formality.value,
        "verbosity": preferences.verbosity.value,
        "topics": sorted(topic.name for topic in preferences.topics),
        "disclosable_facts": sorted(fact.text for fact in preferences.disclosable_facts),
        "authority": sorted(capability.value for capability in preferences.authority.capabilities),
        "transcript_retention_days": preferences.transcript_retention_days,
        "voice": {
            "cloned": preferences.voice.cloned_voice_id,
            "persona": preferences.voice.persona_voice_id,
        },
        "notifications": {
            "on_handled_call": preferences.notifications.on_handled_call,
            "on_blocked_call": preferences.notifications.on_blocked_call,
            "on_missed_escalation": preferences.notifications.on_missed_escalation,
            "daily_summary": preferences.notifications.daily_summary,
            "respect_active_hours": preferences.notifications.respect_active_hours,
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
            "active_hours": _window_to_document(preferences.rules.active_hours),
        },
    }


def document_to_preferences(document: object) -> UserPreferences:
    """Back into the domain, with every value validated on the way.

    A document written by an older version is read with today's defaults for anything it does
    not mention. That is the point of the version field beside it: what a reader cannot supply
    from the document it supplies from the defaults, and a migration can tell which is which.

    Every shape is checked before it is used. A section stored as the wrong kind of value — a
    list where an object belongs, a number where text does — is corruption, and it arrives as
    `InvariantError` like every other corruption here. Left to Python, it would arrive as
    whichever of `AttributeError`, `TypeError` or `ValueError` the first misused value happened
    to raise, and no caller can tell those apart from a bug.
    """
    if not isinstance(document, dict):
        raise InvariantError("stored preferences are not an object")
    rules_document = _section(document, "rules")

    return UserPreferences(
        version=_version(document.get("version")),
        locale=_text(document, "locale", "en"),
        formality=_enum(Formality, document.get("formality"), Formality.NEUTRAL),
        verbosity=_enum(Verbosity, document.get("verbosity"), Verbosity.NORMAL),
        topics=frozenset(Topic(name) for name in _strings(document, "topics")),
        disclosable_facts=frozenset(
            DisclosableFact(text) for text in _strings(document, "disclosable_facts")
        ),
        authority=AgentAuthority(
            frozenset(
                capability
                for value in _items(document, "authority")
                if (capability := _known(Capability, value)) is not None
            )
        ),
        notifications=_notifications_from_document(_section(document, "notifications")),
        voice=_voice_from_document(_section(document, "voice")),
        transcript_retention_days=_retention_days(document.get("transcript_retention_days")),
        important_contacts=tuple(
            _contact_from_document(entry) for entry in _items(document, "important_contacts")
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
                for raw_category, raw_posture in _section(
                    rules_document, "posture_by_category"
                ).items()
                if (category := _known(CallerCategory, raw_category)) is not None
                and (posture := _known(HandlingPosture, raw_posture)) is not None
            },
            blocked_categories=frozenset(
                category
                for raw in _items(rules_document, "blocked_categories")
                if (category := _known(CallerCategory, raw)) is not None
            ),
            escalate_at_or_above=_importance(rules_document.get("escalate_at_or_above")),
            active_hours=_active_hours_from_document(rules_document),
        ),
    )


def _section(document: dict[str, Any], key: str) -> dict[str, Any]:
    """A nested object: empty when absent, and corruption when it is anything but an object."""
    value = document.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvariantError(f"stored preferences hold {key!r} as something other than an object")
    return value


def _items(document: dict[str, Any], key: str) -> list[object]:
    """A nested list: empty when absent, and corruption when it is anything but a list."""
    value = document.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvariantError(f"stored preferences hold {key!r} as something other than a list")
    return value


def _strings(document: dict[str, Any], key: str) -> list[str]:
    """A nested list of text. A member that is not text is corruption, not a newer value."""
    values = _items(document, key)
    texts = [value for value in values if isinstance(value, str)]
    if len(texts) != len(values):
        raise InvariantError(f"stored preferences hold something other than text in {key!r}")
    return texts


def _text(document: dict[str, Any], key: str, default: str) -> str:
    value = document.get(key, default)
    if not isinstance(value, str):
        raise InvariantError(f"stored preferences hold {key!r} as something other than text")
    return value


def _version(raw: object) -> int:
    """The shape the document was written in. Absent reads as today's."""
    if raw is None:
        return PREFERENCES_VERSION
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvariantError("stored preferences carry a version that is not a whole number")
    return raw


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
            step for raw in completed if (step := _known(OnboardingStep, raw)) is not None
        ),
        skipped=frozenset(
            step for raw in skipped if (step := _known(OnboardingStep, raw)) is not None
        ),
    )


def _contact_from_document(entry: object) -> ImportantContact:
    """One stored contact, or the domain's own error rather than a bare `KeyError`.

    A missing key is corruption, and it should arrive as the failure everything else in this
    module raises — otherwise it escapes as a server fault with nothing naming the cause.
    """
    if not isinstance(entry, dict):
        raise InvariantError("a stored contact is not an object")
    for key in ("number", "label"):
        if key not in entry:
            raise InvariantError(f"a stored contact is missing {key!r}")
        if not isinstance(entry[key], str):
            raise InvariantError(f"a stored contact holds {key!r} as something other than text")
    return ImportantContact(
        number=PhoneNumber(entry["number"]),
        label=entry["label"],
        posture=_enum(HandlingPosture, entry.get("posture"), HandlingPosture.PASS_THROUGH),
    )


def _window_to_document(window: TimeWindow | None) -> dict[str, str] | None:
    if window is None:
        return None
    return {
        "start": window.start.strftime("%H:%M"),
        "end": window.end.strftime("%H:%M"),
        "zone": window.zone,
    }


def _window_from_document(document: object) -> TimeWindow | None:
    """No window at all is `None`; a window that is there and unreadable raises.

    `None` and `{}` are not the same thing. The first is a user who set no hours; the
    second is a row that lost them, and treating it as the first is exactly the silent
    disappearance this module argues against everywhere else.
    """
    if document is None:
        return None
    # Raised, not dropped. This is corruption rather than a value from a newer deployment, and
    # the two want opposite handling: an unknown enum member is safely ignored, while quiet hours
    # that silently disappear mean a phone ringing at three in the morning with nothing anywhere
    # to say why.
    if not isinstance(document, dict):
        raise InvariantError("stored hours could not be read: they are not an object")
    parts = [document.get(key) for key in ("start", "end", "zone")]
    start, end, zone = (part if isinstance(part, str) else None for part in parts)
    if start is None or end is None or zone is None:
        raise InvariantError("stored hours could not be read: a start, end or zone is missing")
    try:
        return TimeWindow(start=_parse_time(start), end=_parse_time(end), zone=zone)
    except ValueError as error:
        raise InvariantError(f"stored hours could not be read: {error}") from error


def _active_hours_from_document(rules_document: dict[str, Any]) -> TimeWindow | None:
    """The assistant's hours, including from a document written before there were any (D-030).

    A version 4 document says so directly. An older one has quiet or working hours, and neither
    meant "the assistant answers now": outside a window of assistant hours calls ring the user,
    so turning quiet hours into one would ring somebody through exactly the nights they asked to
    be left alone. Around the clock is the reading that keeps the assistant answering then. An
    older window that is present but corrupt still raises, because it is corruption either way.

    Keyed on the field being present rather than on the version number, so a document that has
    both (written by this version, read after a rollback and a save) is read by what it says.
    """
    if "active_hours" in rules_document:
        return _window_from_document(rules_document["active_hours"])
    for older in ("quiet_hours", "working_hours"):
        _window_from_document(rules_document.get(older))
    return None


def _parse_time(value: str) -> time:
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute))


def _voice_from_document(document: dict[str, Any]) -> VoiceSelection:
    """What the user chose, as far as this version can tell.

    A document written before voices existed has none, which reads as "has not chosen" — and
    that resolves to the provider's default rather than to silence. The version beside the
    document is what makes the distinction readable later.
    """
    return VoiceSelection(
        cloned_voice_id=_optional_text(document.get("cloned")),
        persona_voice_id=_optional_text(document.get("persona")),
    )


def _optional_text(value: object) -> str | None:
    """A stored string, or nothing. Anything else stored here is not a voice identifier."""
    return value if isinstance(value, str) and value.strip() else None


def _retention_days(raw: object) -> int:
    """How long this user keeps transcripts, as stored.

    Absent — a document from before retention was a setting — is the default. Below the floor
    is raised to it: keeping a transcript a day longer is recoverable, and refusing would lock
    somebody out of every other setting over this one. Above the ceiling is kept exactly as
    stored, never lowered: a deployment with a higher ceiling wrote it, and reading it as this
    version's ceiling would purge transcripts earlier than the user chose and write the lower
    number back on the next unrelated save. Anything that is not a whole number is corruption
    and raises, because a retention silently reset is a transcript kept for a length of time
    nobody chose.
    """
    if raw is None:
        return TRANSCRIPT_RETENTION_DEFAULT_DAYS
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise InvariantError("a stored transcript retention is not a whole number of days")
    return max(TRANSCRIPT_RETENTION_FLOOR_DAYS, raw)


def _notifications_from_document(document: dict[str, Any]) -> NotificationPreferences:
    defaults = NotificationPreferences()
    return NotificationPreferences(
        on_handled_call=bool(document.get("on_handled_call", defaults.on_handled_call)),
        on_blocked_call=bool(document.get("on_blocked_call", defaults.on_blocked_call)),
        on_missed_escalation=bool(
            document.get("on_missed_escalation", defaults.on_missed_escalation)
        ),
        daily_summary=bool(document.get("daily_summary", defaults.daily_summary)),
        # The older name for the same boundary (D-030), read when the newer one is absent.
        respect_active_hours=bool(
            document.get(
                "respect_active_hours",
                document.get("respect_quiet_hours", defaults.respect_active_hours),
            )
        ),
    )


def _importance(raw: object) -> CallImportance:
    """The stored threshold, or the default when it is not one this version knows."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return CallImportance.NOTABLE
    try:
        return CallImportance(raw)
    except ValueError:
        return CallImportance.NOTABLE


def _known[E: Enum](kind: type[E], raw: object) -> E | None:
    """The member of `kind` that `raw` is, or None for a value this version does not know."""
    try:
        return kind(raw)
    except ValueError:
        return None


def _enum[E: Enum](kind: type[E], raw: object, fallback: E) -> E:
    """The member of `kind` that `raw` is, or `fallback` for a value this version does not know."""
    known = _known(kind, raw)
    return fallback if known is None else known
