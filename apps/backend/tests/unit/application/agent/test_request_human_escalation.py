"""Asking for a person: the model's reading is noted, and the policy's decision told in words."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.escalation import circumstances_of
from letmehandle.application.agent.tools.escalation import REASON_IN_WORDS, RequestHumanEscalation
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal, decide_escalation
from tests.unit.application.agent.calls import a_call, deferring
from tests.unit.application.agent.kit import Kit, answered, refused

if TYPE_CHECKING:
    from collections.abc import Mapping

NOTABLE_ENQUIRY: Mapping[str, object] = {"importance": "notable", "intent": "enquiry"}


def tool(kit: Kit) -> RequestHumanEscalation:
    return RequestHumanEscalation(kit.notes)


async def test_a_proposal_worth_reaching_the_user_for_is_told_so_and_reaches_nobody_yet() -> None:
    kit = Kit()

    said = await answered(tool(kit), a_call(), NOTABLE_ENQUIRY)

    assert kit.notes.escalations_requested == (
        EscalationProposal(importance=CallImportance.NOTABLE, intent=CallIntent.ENQUIRY),
    )
    assert kit.actions.actions == []
    assert said == (
        "The user's rules call for reaching the user now, because the call matters enough to "
        "interrupt them. That happens once your assessment is recorded."
    )


async def test_every_field_of_the_proposal_is_written_down() -> None:
    kit = Kit()
    arguments = {
        "importance": "routine",
        "intent": "appointment",
        "understood": True,
        "caller_asked_for_the_user": False,
        "needs_the_users_decision": False,
        "requested_capability": "confirm_appointments",
        "caller_summary": "  The dentist wants to confirm Thursday.  ",
    }

    await answered(tool(kit), a_call(from_important_contact=True), arguments)

    assert kit.notes.escalations_requested == (
        EscalationProposal(
            importance=CallImportance.ROUTINE,
            intent=CallIntent.APPOINTMENT,
            requested_capability=Capability.CONFIRM_APPOINTMENTS,
            caller_summary="The dentist wants to confirm Thursday.",
        ),
    )


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("understood", EscalationReason.CANNOT_UNDERSTAND_THE_CALLER),
        ("caller_asked_for_the_user", EscalationReason.CALLER_ASKED_FOR_THE_USER),
        ("needs_the_users_decision", EscalationReason.DECISION_NEEDS_THE_USER),
    ],
)
async def test_each_flag_is_read_as_the_model_sent_it(field: str, reason: EscalationReason) -> None:
    kit = Kit()
    call = a_call()
    value = field != "understood"

    said = await answered(tool(kit), call, {**NOTABLE_ENQUIRY, field: value})

    [proposal] = kit.notes.escalations_requested
    assert decide_escalation(proposal, circumstances_of(call)).reason is reason
    assert REASON_IN_WORDS[reason] in said


async def test_a_decision_that_waits_is_told_the_user_hears_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deferring(monkeypatch)
    kit = Kit()

    said = await answered(tool(kit), a_call(), NOTABLE_ENQUIRY)

    assert said == (
        "The user's rules call for telling the user about this call when it is convenient, not "
        "now, because the call matters enough to interrupt them. That happens once your assessment "
        "is recorded."
    )


async def test_a_proposal_below_the_users_threshold_is_told_nobody_will_be_reached() -> None:
    kit = Kit()

    said = await answered(tool(kit), a_call(), {"importance": "routine", "intent": "sales"})

    assert kit.actions.actions == []
    assert said.startswith("The user's rules do not call for reaching the user on this call.")


async def test_null_optional_fields_are_read_as_absent() -> None:
    kit = Kit()
    arguments = {**NOTABLE_ENQUIRY, "requested_capability": None, "caller_summary": None}

    await answered(tool(kit), a_call(), arguments)

    [proposal] = kit.notes.escalations_requested
    assert proposal.requested_capability is None
    assert proposal.caller_summary is None


@pytest.mark.parametrize("waits", [False, True])
async def test_nothing_the_model_is_told_asks_it_to_speak_to_anybody(
    waits: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The judging agent talks to nobody, so it is never told to tell the caller anything.
    if waits:
        deferring(monkeypatch)
    for arguments in [NOTABLE_ENQUIRY, {"importance": "routine", "intent": "sales"}]:
        said = await answered(tool(Kit()), a_call(), arguments)
        assert "caller" not in said.lower()


def test_every_reason_the_policy_can_give_can_be_said() -> None:
    assert set(REASON_IN_WORDS) == set(EscalationReason)


MALFORMED: list[tuple[Mapping[str, object], str]] = [
    ({}, "importance is required"),
    ({"intent": "enquiry"}, "importance is required"),
    ({"importance": "notable"}, "intent is required"),
    ({**NOTABLE_ENQUIRY, "importance": 40}, "importance must be one of"),
    ({**NOTABLE_ENQUIRY, "importance": "NOTABLE"}, "importance must be one of"),
    ({**NOTABLE_ENQUIRY, "importance": "critical"}, "importance must be one of"),
    ({**NOTABLE_ENQUIRY, "importance": True}, "importance must be one of"),
    ({**NOTABLE_ENQUIRY, "intent": "emergency"}, "intent must be one of"),
    ({**NOTABLE_ENQUIRY, "understood": "yes"}, "understood must be true or false"),
    ({**NOTABLE_ENQUIRY, "caller_asked_for_the_user": 1}, "caller_asked_for_the_user must be"),
    ({**NOTABLE_ENQUIRY, "needs_the_users_decision": None}, "needs_the_users_decision must be"),
    ({**NOTABLE_ENQUIRY, "requested_capability": "do_anything"}, "requested_capability must be"),
    ({**NOTABLE_ENQUIRY, "requested_capability": ["take_a_message"]}, "requested_capability"),
    ({**NOTABLE_ENQUIRY, "caller_summary": 7}, "caller_summary must be text"),
    ({**NOTABLE_ENQUIRY, "caller_summary": " \n "}, "caller_summary must not be blank"),
    ({**NOTABLE_ENQUIRY, "caller_summary": "x" * 281}, "caller_summary is at most 280"),
    ({**NOTABLE_ENQUIRY, "reason": "caller_asked_for_the_user"}, "unexpected arguments: reason"),
    ({**NOTABLE_ENQUIRY, "urgency": "immediate"}, "unexpected arguments: urgency"),
]


@pytest.mark.parametrize(("arguments", "because"), MALFORMED)
async def test_a_malformed_proposal_is_refused_and_reaches_nobody(
    arguments: Mapping[str, object], because: str
) -> None:
    kit = Kit()
    call = a_call(authority=AgentAuthority.granting(*Capability))

    reason = await refused(tool(kit), call, arguments)

    assert reason.startswith(because)
    assert kit.notes.escalations_requested == ()
    assert [refusal.reason for refusal in kit.notes.refusals] == [reason]
