"""An escalation decision carries a reason and urgency exactly when required."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)


def test_not_escalating_carries_nothing() -> None:
    decision = EscalationDecision.not_needed()
    assert not decision.required
    assert decision.reason is None
    assert decision.urgency is None


def test_escalating_carries_a_reason_and_an_urgency() -> None:
    decision = EscalationDecision.needed(
        EscalationReason.CALLER_ASKED_FOR_THE_USER, EscalationUrgency.IMMEDIATE
    )
    assert decision.required
    assert decision.reason is EscalationReason.CALLER_ASKED_FOR_THE_USER
    assert decision.is_immediate


def test_an_escalation_without_a_reason_cannot_be_constructed() -> None:
    with pytest.raises(InvariantError, match="why"):
        EscalationDecision(required=True)


def test_an_escalation_without_an_urgency_cannot_be_constructed() -> None:
    with pytest.raises(InvariantError):
        EscalationDecision(required=True, reason=EscalationReason.DECISION_NEEDS_THE_USER)


def test_a_non_escalation_carrying_a_reason_cannot_be_constructed() -> None:
    with pytest.raises(InvariantError):
        EscalationDecision(required=False, reason=EscalationReason.DECISION_NEEDS_THE_USER)


def test_an_empty_caller_summary_is_rejected() -> None:
    with pytest.raises(InvariantError):
        EscalationDecision.needed(
            EscalationReason.DECISION_NEEDS_THE_USER,
            EscalationUrgency.IMMEDIATE,
            caller_summary="  ",
        )


def test_the_caller_summary_is_what_the_user_reads_before_answering() -> None:
    decision = EscalationDecision.needed(
        EscalationReason.DECISION_NEEDS_THE_USER,
        EscalationUrgency.IMMEDIATE,
        caller_summary="A courier is at the gate and needs to know where to leave a parcel.",
    )
    assert decision.caller_summary is not None
    assert "courier" in decision.caller_summary


def test_urgency_distinguishes_ringing_now_from_telling_them_later() -> None:
    convenient = EscalationDecision.needed(
        EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT, EscalationUrgency.WHILE_CONVENIENT
    )
    assert convenient.required
    assert not convenient.is_immediate
