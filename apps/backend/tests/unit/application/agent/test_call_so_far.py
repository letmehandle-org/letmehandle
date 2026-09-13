"""A call built from one user's preferences, so what the model reads and what is enforced agree."""

from __future__ import annotations

import pytest

from letmehandle.application.agent.ports import CallSoFar
from letmehandle.application.preferences.context import build_preference_context
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.call import Speaker, TranscriptEntry
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, ImportantContact, UserPreferences
from tests.unit.application.agent.calls import ACTIVE, NOON

PARTNER = ImportantContact(PhoneNumber("+12025550143"), "Partner")
SCHOOL = ImportantContact(PhoneNumber("+12025550187"), "School office")
PREFERENCES = UserPreferences(
    rules=CallRules(active_hours=ACTIVE, escalate_at_or_above=CallImportance.URGENT),
    authority=AgentAuthority.granting(Capability.TAKE_A_MESSAGE),
    important_contacts=(PARTNER, SCHOOL),
)


def call_from(caller: Caller) -> CallSoFar:
    return CallSoFar.for_user(
        call_id=CallId("call-1"),
        preferences=PREFERENCES,
        caller=caller,
        transcript=iter([TranscriptEntry(Speaker.CALLER, "Hello.", NOON)]),
        now=NOON,
    )


def test_every_copy_of_the_users_rules_comes_from_the_same_preferences() -> None:
    call = call_from(Caller(number=PhoneNumber("+12025550101")))

    assert call.authority == PREFERENCES.authority
    assert call.rules == PREFERENCES.rules
    assert call.preferences == build_preference_context(PREFERENCES, now=NOON)
    assert call.preferences.granted_capabilities == (Capability.TAKE_A_MESSAGE,)
    assert call.preferences.escalate_at_or_above is call.rules.escalate_at_or_above
    assert call.transcript == (TranscriptEntry(Speaker.CALLER, "Hello.", NOON),)
    assert call.now == NOON


def test_an_important_contact_is_known_by_number_and_named_by_the_users_label() -> None:
    # The name the network supplied is not the user's, and is not what the call carries.
    call = call_from(
        Caller(number=SCHOOL.number, display_name="Unknown", category=CallerCategory.EDUCATION)
    )

    assert call.from_important_contact
    assert call.contact_label == "School office"


@pytest.mark.parametrize(
    "caller",
    [
        pytest.param(Caller(number=PhoneNumber("+12025550101")), id="a stranger"),
        pytest.param(Caller(display_name="Partner"), id="a withheld number with a familiar name"),
    ],
)
def test_anyone_else_is_neither_important_nor_labelled(caller: Caller) -> None:
    call = call_from(caller)

    assert not call.from_important_contact
    assert call.contact_label is None
