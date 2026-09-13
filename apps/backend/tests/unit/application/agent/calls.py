"""Calls to judge, built the way orchestration will build them.

Everything is derived from one `UserPreferences`, so the authority a tool enforces and the context
a model reads start out agreeing — and a test that wants them to disagree has to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import CallSoFar
from letmehandle.application.preferences.context import build_preference_context
from letmehandle.domain.models.call import Speaker, TranscriptEntry
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, TimeWindow, UserPreferences

if TYPE_CHECKING:
    from collections.abc import Iterable

    from letmehandle.domain.models.authority import AgentAuthority

# Winter, so London is on UTC and the active window reads the same in both. Noon is inside it and
# three in the morning outside.
NOON: Final = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
THREE_AM: Final = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
ACTIVE: Final = TimeWindow(time(7, 0), time(22, 0), "Europe/London")
RULES: Final = CallRules(active_hours=ACTIVE, escalate_at_or_above=CallImportance.NOTABLE)

# Reserved for fiction, never routable.
STRANGER_NUMBER: Final = PhoneNumber("+12025550101")
STRANGER: Final = Caller(number=STRANGER_NUMBER, category=CallerCategory.UNKNOWN)


def said(*lines: str) -> tuple[TranscriptEntry, ...]:
    """What a caller said, one entry per line."""
    return tuple(TranscriptEntry(Speaker.CALLER, line, NOON) for line in lines)


def a_call(
    *,
    call_id: str = "call-1",
    preferences: UserPreferences | None = None,
    authority: AgentAuthority | None = None,
    caller: Caller = STRANGER,
    transcript: Iterable[TranscriptEntry] = (),
    now: datetime = NOON,
    from_important_contact: bool = False,
    contact_label: str | None = None,
) -> CallSoFar:
    """A call. `authority`, if given, replaces the one the preferences grant."""
    chosen = preferences if preferences is not None else UserPreferences(rules=RULES)
    return CallSoFar(
        call_id=CallId(call_id),
        caller=caller,
        transcript=tuple(transcript),
        preferences=build_preference_context(chosen, now=now),
        authority=authority if authority is not None else chosen.authority,
        rules=chosen.rules,
        from_important_contact=from_important_contact,
        now=now,
        contact_label=contact_label,
    )
