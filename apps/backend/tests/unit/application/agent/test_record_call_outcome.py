"""Recording how a call went, within the limits the summary will hold it to."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.ports import OutcomeRecord
from letmehandle.application.agent.tools.outcome import MAX_DETAILS, RecordCallOutcome
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome, ExtractedDetail
from tests.support.recording_call_actions import Recorded
from tests.unit.application.agent.calls import a_call
from tests.unit.application.agent.kit import Kit, answered, refused

if TYPE_CHECKING:
    from collections.abc import Mapping

RESOLVED: Mapping[str, object] = {
    "outcome": "resolved_by_agent",
    "headline": "Parcel left at the gate.",
}
REFERENCE = {"label": "reference number", "value": "AB-1234", "evidence": "it's AB-1234"}


async def test_an_outcome_with_details_is_recorded() -> None:
    kit = Kit()
    call = a_call()
    arguments = {**RESOLVED, "details": [REFERENCE, {"label": " time ", "value": "after four"}]}

    said = await answered(RecordCallOutcome(kit.notes, kit.actions), call, arguments)

    assert kit.actions.actions == [
        Recorded(
            call.call_id,
            OutcomeRecord(
                outcome=CallOutcome.RESOLVED_BY_AGENT,
                headline="Parcel left at the gate.",
                details=(
                    ExtractedDetail("reference number", "AB-1234", "it's AB-1234"),
                    ExtractedDetail("time", "after four"),
                ),
            ),
        )
    ]
    assert said == "The outcome has been recorded."


@pytest.mark.parametrize("details", [None, []])
async def test_no_details_is_an_outcome_without_any(details: object) -> None:
    kit = Kit()

    await answered(
        RecordCallOutcome(kit.notes, kit.actions), a_call(), {**RESOLVED, "details": details}
    )

    assert kit.actions.of_kind(Recorded)[0].record.details == ()


async def test_a_headline_and_details_at_their_limits_are_recorded_whole() -> None:
    kit = Kit()
    headline = "h" * MAX_HEADLINE_CHARACTERS
    arguments = {"outcome": "failed", "headline": headline, "details": [REFERENCE] * MAX_DETAILS}

    await answered(RecordCallOutcome(kit.notes, kit.actions), a_call(), arguments)

    (recorded,) = kit.actions.of_kind(Recorded)
    assert recorded.record.headline == headline
    assert len(recorded.record.details) == MAX_DETAILS


MALFORMED: list[tuple[Mapping[str, object], str]] = [
    ({"headline": "Parcel."}, "outcome is required"),
    ({"outcome": "went_well", "headline": "Parcel."}, "outcome must be one of"),
    ({"outcome": "resolved_by_agent"}, "headline is required"),
    ({**RESOLVED, "headline": ""}, "headline must not be blank"),
    ({**RESOLVED, "headline": ["Parcel."]}, "headline must be text"),
    ({**RESOLVED, "headline": "h" * (MAX_HEADLINE_CHARACTERS + 1)}, "headline is at most 280"),
    ({**RESOLVED, "details": "reference AB-1234"}, "details must be a list"),
    ({**RESOLVED, "details": {"label": "a", "value": "b"}}, "details must be a list"),
    ({**RESOLVED, "details": [REFERENCE, "AB-1234"]}, "every entry in details must be an object"),
    ({**RESOLVED, "details": [REFERENCE] * (MAX_DETAILS + 1)}, "details holds at most 12"),
    ({**RESOLVED, "details": [{"value": "AB-1234"}]}, "label is required"),
    ({**RESOLVED, "details": [{"label": "reference", "value": " "}]}, "value must not be blank"),
    ({**RESOLVED, "details": [{"label": "l" * 81, "value": "v"}]}, "label is at most 80"),
    ({**RESOLVED, "details": [{**REFERENCE, "evidence": "e" * 281}]}, "evidence is at most 280"),
    (
        {**RESOLVED, "details": [{**REFERENCE, "number": "+12025550199"}]},
        "unexpected arguments: number",
    ),
    ({**RESOLVED, "summary": "everything"}, "unexpected arguments: summary"),
]


@pytest.mark.parametrize(("arguments", "because"), MALFORMED)
async def test_a_malformed_outcome_is_refused_and_nothing_is_recorded(
    arguments: Mapping[str, object], because: str
) -> None:
    kit = Kit()

    reason = await refused(RecordCallOutcome(kit.notes, kit.actions), a_call(), arguments)

    assert reason.startswith(because)
    assert kit.actions.actions == []
