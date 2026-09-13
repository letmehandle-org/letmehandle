"""A call summary stands on its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.summary import (
    MAX_HEADLINE_CHARACTERS,
    CallOutcome,
    CallSummary,
    ExtractedDetail,
)

START = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
END = START + timedelta(seconds=95)


def a_summary(**overrides: object) -> CallSummary:
    fields: dict[str, object] = {
        "call_id": CallId("call-1"),
        "caller": Caller(category=CallerCategory.DELIVERY),
        "intent": CallIntent.DELIVERY_IN_PROGRESS,
        "importance": CallImportance.NOTABLE,
        "outcome": CallOutcome.RESOLVED_BY_AGENT,
        "headline": "A courier left a parcel behind the side gate.",
        "started_at": START,
        "ended_at": END,
    }
    fields.update(overrides)
    return CallSummary(**fields)  # type: ignore[arg-type]


def test_a_summary_records_what_happened_and_how_long_it_took() -> None:
    summary = a_summary()
    assert summary.outcome is CallOutcome.RESOLVED_BY_AGENT
    assert summary.duration_seconds == 95
    assert not summary.human_joined


def test_a_summary_with_no_headline_is_refused() -> None:
    with pytest.raises(InvariantError):
        a_summary(headline="   ")


def test_a_headline_longer_than_a_summary_is_refused() -> None:
    with pytest.raises(InvariantError, match="transcript with extra steps"):
        a_summary(headline="x" * (MAX_HEADLINE_CHARACTERS + 1))


def test_a_headline_at_the_limit_is_accepted() -> None:
    assert len(a_summary(headline="x" * MAX_HEADLINE_CHARACTERS).headline) == 280


def test_a_call_cannot_end_before_it_started() -> None:
    with pytest.raises(InvariantError):
        a_summary(started_at=END, ended_at=START)


def test_a_call_the_user_joined_records_when_and_why() -> None:
    summary = a_summary(
        outcome=CallOutcome.HANDED_TO_USER,
        human_joined_at=START + timedelta(seconds=30),
        escalation_reason=EscalationReason.DECISION_NEEDS_THE_USER,
    )
    assert summary.human_joined
    assert summary.escalation_reason is EscalationReason.DECISION_NEEDS_THE_USER


def test_a_join_without_a_reason_is_refused() -> None:
    with pytest.raises(InvariantError, match="reason"):
        a_summary(human_joined_at=START + timedelta(seconds=30))


@pytest.mark.parametrize("offset", [-1, 96])
def test_the_user_cannot_have_joined_outside_the_call(offset: int) -> None:
    with pytest.raises(InvariantError):
        a_summary(
            human_joined_at=START + timedelta(seconds=offset),
            escalation_reason=EscalationReason.DECISION_NEEDS_THE_USER,
        )


class TestExtractedDetails:
    def test_a_detail_carries_its_evidence(self) -> None:
        detail = ExtractedDetail(
            label="reference",
            value="AB1234",
            evidence="the reference is AB1234",
        )
        assert detail.evidence is not None
        assert detail.value in detail.evidence

    @pytest.mark.parametrize(("label", "value"), [("", "x"), ("x", ""), ("  ", "x")])
    def test_a_detail_missing_either_half_is_refused(self, label: str, value: str) -> None:
        with pytest.raises(InvariantError):
            ExtractedDetail(label=label, value=value)

    def test_details_are_looked_up_by_label(self) -> None:
        summary = a_summary(
            details=(
                ExtractedDetail("reference", "AB1234"),
                ExtractedDetail("left_with", "the side gate"),
            )
        )
        found = summary.detail("left_with")
        assert found is not None
        assert found.value == "the side gate"

    def test_a_label_that_was_not_found_returns_nothing(self) -> None:
        assert a_summary().detail("reference") is None


def test_a_summary_repr_quotes_nothing_from_the_call() -> None:
    summary = a_summary(headline="Caller asked for the card PIN")
    assert "card PIN" not in repr(summary)
