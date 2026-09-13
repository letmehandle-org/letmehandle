"""The escalation policy, as a table of calls and expected decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from typing import Final

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.preferences import CallRules, TimeWindow
from letmehandle.domain.policy.escalation import (
    CallCircumstances,
    EscalationProposal,
    decide_escalation,
    most_pressing,
)

# The assistant answers from seven in the morning to ten at night.
ACTIVE: Final = TimeWindow(time(7, 0), time(22, 0), "Europe/London")

RULES: Final = CallRules(active_hours=ACTIVE, escalate_at_or_above=CallImportance.NOTABLE)
MAY_TAKE_A_MESSAGE: Final = AgentAuthority.granting(Capability.TAKE_A_MESSAGE)

NOTABLE_ENQUIRY: Final = EscalationProposal(
    importance=CallImportance.NOTABLE, intent=CallIntent.ENQUIRY
)

IMMEDIATE: Final = EscalationUrgency.IMMEDIATE


def circumstances(
    *,
    rules: CallRules = RULES,
    authority: AgentAuthority = MAY_TAKE_A_MESSAGE,
    important_contact: bool = False,
) -> CallCircumstances:
    return CallCircumstances(
        rules=rules, authority=authority, from_important_contact=important_contact
    )


def call(**changes: object) -> EscalationProposal:
    return replace(NOTABLE_ENQUIRY, **changes)  # type: ignore[arg-type]  # fields vary per row


ESCALATED: Final = [
    pytest.param(
        call(),
        circumstances(),
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT,
        IMMEDIATE,
        id="a notable call at noon rings",
    ),
    pytest.param(
        call(understood=False),
        circumstances(),
        EscalationReason.CANNOT_UNDERSTAND_THE_CALLER,
        IMMEDIATE,
        id="not understanding outranks every other reason",
    ),
    pytest.param(
        call(caller_asked_for_the_user=True, needs_the_users_decision=True),
        circumstances(),
        EscalationReason.CALLER_ASKED_FOR_THE_USER,
        IMMEDIATE,
        id="asking for the user outranks a decision",
    ),
    pytest.param(
        call(requested_capability=Capability.CONFIRM_APPOINTMENTS, needs_the_users_decision=True),
        circumstances(),
        EscalationReason.ACTION_NOT_AUTHORISED,
        IMMEDIATE,
        id="an ungranted action outranks a decision",
    ),
    pytest.param(
        call(needs_the_users_decision=True),
        circumstances(),
        EscalationReason.DECISION_NEEDS_THE_USER,
        IMMEDIATE,
        id="a decision outranks importance",
    ),
    pytest.param(
        call(importance=CallImportance.LOW, caller_asked_for_the_user=True),
        circumstances(important_contact=True),
        EscalationReason.CALLER_ASKED_FOR_THE_USER,
        IMMEDIATE,
        id="an important contact clears the threshold whatever the importance",
    ),
    pytest.param(
        call(
            intent=CallIntent.SUSPECTED_FRAUD,
            importance=CallImportance.URGENT,
            caller_asked_for_the_user=True,
        ),
        circumstances(important_contact=True),
        EscalationReason.CALLER_ASKED_FOR_THE_USER,
        IMMEDIATE,
        id="an important contact the model suspects of fraud still reaches the user",
    ),
    pytest.param(
        call(),
        circumstances(rules=replace(RULES, active_hours=None)),
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT,
        IMMEDIATE,
        id="around the clock escalates exactly as set hours do",
    ),
    pytest.param(
        call(importance=CallImportance.ROUTINE),
        circumstances(rules=replace(RULES, escalate_at_or_above=CallImportance.ROUTINE)),
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT,
        IMMEDIATE,
        id="the user's own threshold is the one a call is held to",
    ),
]


@pytest.mark.parametrize(("proposal", "situation", "reason", "urgency"), ESCALATED)
def test_calls_that_reach_the_user(
    proposal: EscalationProposal,
    situation: CallCircumstances,
    reason: EscalationReason,
    urgency: EscalationUrgency,
) -> None:
    decision = decide_escalation(proposal, situation)
    assert decision == EscalationDecision.needed(reason, urgency)


NOT_ESCALATED: Final = [
    pytest.param(
        call(importance=CallImportance.ROUTINE),
        circumstances(),
        id="a routine call with nothing to ask does not ring",
    ),
    pytest.param(
        call(
            intent=CallIntent.SUSPECTED_FRAUD,
            importance=CallImportance.URGENT,
            caller_asked_for_the_user=True,
        ),
        circumstances(),
        id="a suspected scam never reaches the user, however it is dressed up",
    ),
    pytest.param(
        call(importance=CallImportance.LOW, caller_asked_for_the_user=True),
        circumstances(),
        id="asking for the user is not enough below the threshold",
    ),
    pytest.param(
        call(importance=CallImportance.LOW, requested_capability=Capability.CONFIRM_APPOINTMENTS),
        circumstances(),
        id="an ungranted action on an unimportant call waits for the summary",
    ),
    pytest.param(
        call(requested_capability=Capability.TAKE_A_MESSAGE, importance=CallImportance.ROUTINE),
        circumstances(),
        id="a granted action needs nobody",
    ),
    pytest.param(
        call(importance=CallImportance.LOW),
        circumstances(important_contact=True),
        id="an important contact with nothing to ask is handled",
    ),
    pytest.param(
        call(importance=CallImportance.NOTABLE),
        circumstances(rules=replace(RULES, escalate_at_or_above=CallImportance.URGENT)),
        id="one step below the user's threshold does not ring",
    ),
]


@pytest.mark.parametrize(("proposal", "situation"), NOT_ESCALATED)
def test_calls_that_do_not(proposal: EscalationProposal, situation: CallCircumstances) -> None:
    assert decide_escalation(proposal, situation) == EscalationDecision.not_needed()


@pytest.mark.parametrize("importance", list(CallImportance))
@pytest.mark.parametrize("threshold", list(CallImportance))
def test_the_threshold_boundary_holds_for_every_pair(
    importance: CallImportance, threshold: CallImportance
) -> None:
    rules = replace(RULES, active_hours=None, escalate_at_or_above=threshold)
    decision = decide_escalation(call(importance=importance), circumstances(rules=rules))
    assert decision.required is (importance >= threshold)


def test_the_callers_summary_travels_with_the_decision() -> None:
    summary = "a courier at the gate needs to know where to leave a parcel"
    decision = decide_escalation(call(caller_summary=summary), circumstances())
    assert decision.caller_summary == summary


def test_the_same_call_always_gets_the_same_answer() -> None:
    situation = circumstances()
    proposal = call(needs_the_users_decision=True, importance=CallImportance.URGENT)
    assert len({decide_escalation(proposal, situation) for _ in range(20)}) == 1


def test_a_blank_summary_is_refused() -> None:
    with pytest.raises(InvariantError):
        call(caller_summary="   ")


def test_changing_what_the_user_granted_changes_the_decision_for_the_same_call() -> None:
    proposal = call(requested_capability=Capability.CONFIRM_APPOINTMENTS)
    without = decide_escalation(proposal, circumstances())
    granted = decide_escalation(
        proposal, circumstances(authority=AgentAuthority.granting(Capability.CONFIRM_APPOINTMENTS))
    )
    assert without.reason is EscalationReason.ACTION_NOT_AUTHORISED
    assert granted.reason is EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT


URGENT_PERSONAL: Final = call(importance=CallImportance.URGENT, intent=CallIntent.PERSONAL)
ROUTINE_ENQUIRY: Final = call(importance=CallImportance.ROUTINE)


@pytest.mark.parametrize(
    ("readings", "expected"),
    [
        pytest.param((ROUTINE_ENQUIRY,), ROUTINE_ENQUIRY, id="one reading is the reading"),
        pytest.param(
            (URGENT_PERSONAL, ROUTINE_ENQUIRY), URGENT_PERSONAL, id="an earlier alarm outlasts calm"
        ),
        pytest.param(
            (ROUTINE_ENQUIRY, URGENT_PERSONAL), URGENT_PERSONAL, id="a later alarm is heard"
        ),
        pytest.param((ROUTINE_ENQUIRY, call()), call(), id="reaching the user beats nothing"),
        pytest.param(
            (call(caller_summary="first"), call(caller_summary="last")),
            call(caller_summary="last"),
            id="of readings the rules treat alike, the last",
        ),
    ],
)
def test_the_most_pressing_reading_is_the_one_acted_on(
    readings: tuple[EscalationProposal, ...], expected: EscalationProposal
) -> None:
    assert most_pressing(readings, circumstances()) == expected


def test_no_readings_have_no_most_pressing_one() -> None:
    with pytest.raises(InvariantError):
        most_pressing((), circumstances())
