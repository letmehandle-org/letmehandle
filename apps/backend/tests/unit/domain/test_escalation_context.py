"""The stored context of an escalation refuses states the app could not show honestly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    MAX_CALL_ID_LENGTH,
    MAX_CALLER_LABEL_LENGTH,
    MAX_DETAIL_LENGTH,
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
from letmehandle.domain.models.identifiers import CallId

RAISED = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)


def context(**overrides: object) -> EscalationContext:
    values: dict[str, object] = {
        "call_id": CallId("call-1"),
        "reason": EscalationReason.CALLER_ASKED_FOR_THE_USER,
        "raised_at": RAISED,
        "caller_label": "a courier",
        "established": "They are at the gate.",
        "needed": "Where to leave the parcel.",
    }
    values.update(overrides)
    return EscalationContext(**values)  # type: ignore[arg-type]


def test_a_new_context_is_live_and_not_yet_delivered() -> None:
    made = context()
    assert made.is_live
    assert made.status is EscalationStatus.LIVE
    assert made.delivery is NotificationDelivery.PENDING


def test_only_the_reason_is_required() -> None:
    # Early in a call nothing may be known: who it is, or what they want.
    made = context(caller_label=None, established=None, needed=None)
    assert made.caller_label is None


@pytest.mark.parametrize("field", ["caller_label", "established", "needed"])
def test_a_blank_detail_is_refused(field: str) -> None:
    with pytest.raises(InvariantError, match="absent or says something"):
        context(**{field: "   "})


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("caller_label", MAX_CALLER_LABEL_LENGTH),
        ("established", MAX_DETAIL_LENGTH),
        ("needed", MAX_DETAIL_LENGTH),
    ],
)
def test_an_overlong_detail_is_refused_and_the_limit_is_allowed(field: str, limit: int) -> None:
    assert getattr(context(**{field: "x" * limit}), field) == "x" * limit
    with pytest.raises(InvariantError, match="longer than"):
        context(**{field: "x" * (limit + 1)})


def test_a_call_id_too_long_to_bind_a_notification_is_refused() -> None:
    context(call_id=CallId("c" * MAX_CALL_ID_LENGTH))
    with pytest.raises(InvariantError, match="call id"):
        context(call_id=CallId("c" * (MAX_CALL_ID_LENGTH + 1)))


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(InvariantError, match="zone"):
        context(raised_at=datetime(2026, 1, 5, 9, 30))


def test_an_end_time_goes_with_the_ended_status_and_only_with_it() -> None:
    with pytest.raises(InvariantError, match="exactly when"):
        context(status=EscalationStatus.ENDED)
    with pytest.raises(InvariantError, match="exactly when"):
        context(ended_at=RAISED)


def test_a_call_cannot_end_before_the_escalation() -> None:
    with pytest.raises(InvariantError, match="before"):
        context(status=EscalationStatus.ENDED, ended_at=RAISED - timedelta(seconds=1))


def test_ending_records_the_first_end_and_keeps_it() -> None:
    first = RAISED + timedelta(minutes=2)
    ended = context().ended(first)
    assert not ended.is_live
    assert ended.ended_at == first
    assert ended.ended(first + timedelta(minutes=5)).ended_at == first


def test_an_end_a_moment_before_the_escalation_is_recorded_at_the_escalation() -> None:
    ended = context().ended(RAISED - timedelta(milliseconds=5))
    assert not ended.is_live
    assert ended.ended_at == RAISED
