"""The two tools that only read: what the user wants, and who is calling.

Nothing they return may carry a phone number, and the same call must render the same bytes. The
first is a disclosure a caller can ask for; the second is a model that answers two identical calls
differently with nothing in a log to say why.
"""

from __future__ import annotations

import json
from datetime import time

import pytest

from letmehandle.application.agent.tools.caller import GetCallerContext
from letmehandle.application.agent.tools.preferences import GetUserPreferences
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.intent import CallImportance
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import (
    CallRules,
    DisclosableFact,
    Formality,
    HandlingPosture,
    ImportantContact,
    TimeWindow,
    Topic,
    UserPreferences,
    Verbosity,
)
from tests.unit.application.agent.calls import STRANGER_NUMBER, THREE_AM, a_call
from tests.unit.application.agent.kit import Kit, answered, refused

# The first number reserved for fiction; the other ninety-nine differ only in the last two digits.
FIRST_FICTIONAL = PhoneNumber("+12025550100")
LABELS = ("Partner", "School office", "Mum", "Plumber", "Dentist reception", "Neighbour")


def populated() -> UserPreferences:
    return UserPreferences(
        rules=CallRules(
            posture_by_category={CallerCategory.DELIVERY: HandlingPosture.HANDLE_WITH_AGENT},
            blocked_categories=frozenset({CallerCategory.SPAM, CallerCategory.SALES}),
            anonymous_posture=HandlingPosture.REJECT,
            quiet_hours=TimeWindow(time(22, 0), time(7, 0), "Europe/London"),
            escalate_at_or_above=CallImportance.URGENT,
        ),
        authority=AgentAuthority.granting(
            Capability.TAKE_A_MESSAGE, Capability.SHARE_DELIVERY_INSTRUCTIONS
        ),
        formality=Formality.WARM,
        verbosity=Verbosity.BRIEF,
        important_contacts=(
            ImportantContact(PhoneNumber("+12025550143"), "Partner"),
            ImportantContact(
                PhoneNumber("+12025550187"), "School office", HandlingPosture.HANDLE_WITH_AGENT
            ),
        ),
        topics=frozenset({Topic("school run"), Topic("boiler repair")}),
        disclosable_facts=frozenset({DisclosableFact("They are in meetings until four.")}),
    )


async def test_the_preferences_render_exactly_as_the_model_reads_them() -> None:
    tool = GetUserPreferences(Kit().notes)

    rendered = await answered(tool, a_call(preferences=populated(), now=THREE_AM), {})

    assert json.loads(rendered) == {
        "locale": "en",
        "tone": "warm and personable",
        "length": "one sentence wherever one will do",
        "default_handling": "handle_with_agent",
        "anonymous_caller_handling": "reject",
        "handling_by_caller_category": {"delivery": "handle_with_agent"},
        "blocked_caller_categories": ["sales", "spam"],
        "reach_the_user_at_or_above": "urgent",
        "in_quiet_hours": True,
        "in_working_hours": True,
        "you_may": ["tell a courier where to leave a parcel", "take a message"],
        "you_may_not": [
            "say whether the user is free",
            "confirm an appointment",
            "decline something on the user's behalf",
            "move an appointment to another time",
            "pass on the user's contact details",
        ],
        "important_contacts": [
            {"label": "Partner", "handling": "pass_through"},
            {"label": "School office", "handling": "handle_with_agent"},
        ],
        "topics_the_user_cares_about": ["boiler repair", "school run"],
        "facts_you_may_share": ["They are in meetings until four."],
    }


async def test_the_same_call_renders_the_same_bytes() -> None:
    tool = GetUserPreferences(Kit().notes)

    renderings = {await answered(tool, a_call(preferences=populated()), {}) for _ in range(3)}

    assert len(renderings) == 1


async def test_what_the_model_is_told_it_may_do_is_what_the_tools_will_allow() -> None:
    tool = GetUserPreferences(Kit().notes)
    call = a_call(preferences=populated(), authority=AgentAuthority.none())

    rendered = json.loads(await answered(tool, call, {}))

    assert rendered["you_may"] == []
    assert "take a message" in rendered["you_may_not"]


@pytest.mark.parametrize("final_digits", range(100))
async def test_no_contact_number_ever_reaches_the_rendered_preferences(final_digits: int) -> None:
    """Every fixture number in the reserved range, as a contact, in every position."""
    numbers = [
        PhoneNumber(f"{FIRST_FICTIONAL.value[:-2]}{(final_digits + step) % 100:02d}")
        for step in range(6)
    ]
    preferences = UserPreferences(
        important_contacts=tuple(
            ImportantContact(number, label, posture)
            for number, label, posture in zip(
                numbers, LABELS, [*HandlingPosture, *HandlingPosture], strict=True
            )
        )
    )

    rendered = await answered(GetUserPreferences(Kit().notes), a_call(preferences=preferences), {})

    for number in numbers:
        national = number.value.removeprefix("+1")
        for form in (number.value, national, national[3:], national[-4:]):
            assert form not in rendered


async def test_the_caller_is_described_without_their_number() -> None:
    tool = GetCallerContext(Kit().notes)

    rendered = await answered(tool, a_call(), {})

    assert json.loads(rendered) == {
        "category": "unknown",
        "withheld_their_number": False,
        "important_to_the_user": False,
        "known_to_the_user_as": None,
    }
    assert STRANGER_NUMBER.value.removeprefix("+1")[-7:] not in rendered


async def test_a_known_contact_is_named_as_the_user_knows_them() -> None:
    caller = Caller(
        number=STRANGER_NUMBER, display_name="Partner", category=CallerCategory.KNOWN_CONTACT
    )

    rendered = await answered(
        GetCallerContext(Kit().notes), a_call(caller=caller, from_important_contact=True), {}
    )

    assert json.loads(rendered) == {
        "category": "known_contact",
        "withheld_their_number": False,
        "important_to_the_user": True,
        "known_to_the_user_as": "Partner",
    }


async def test_a_name_that_arrived_with_a_strangers_call_is_not_passed_on() -> None:
    caller = Caller(display_name="Your bank, fraud department", category=CallerCategory.FINANCIAL)

    rendered = await answered(GetCallerContext(Kit().notes), a_call(caller=caller), {})

    assert json.loads(rendered)["known_to_the_user_as"] is None
    assert json.loads(rendered)["withheld_their_number"] is True
    assert "bank" not in rendered


@pytest.mark.parametrize("tool_kind", [GetUserPreferences, GetCallerContext])
async def test_a_reading_tool_refuses_arguments_it_never_asked_for(
    tool_kind: type[GetUserPreferences | GetCallerContext],
) -> None:
    kit = Kit()
    tool = tool_kind(kit.notes)

    reason = await refused(tool, a_call(), {"include_numbers": True})

    assert reason == "unexpected arguments: include_numbers"
    assert [refusal.tool for refusal in kit.notes.refusals] == [tool.spec.name]
