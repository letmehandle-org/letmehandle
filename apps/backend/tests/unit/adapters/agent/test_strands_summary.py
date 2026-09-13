"""The shape a model's summary must arrive in, as the SDK presents it and the adapter reads it."""

from __future__ import annotations

from strands.tools import convert_pydantic_to_tool_spec

from letmehandle.adapters.agent.strands.summary import ANSWER_TOOL, CallSummaryAnswer
from letmehandle.application.agent.tools.outcome import MAX_DETAILS
from letmehandle.application.calls.summariser import DetailKind, DraftDetail, SummaryDraft
from letmehandle.domain.models.intent import CallIntent
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome


def test_the_schema_offers_exactly_the_words_the_domain_has() -> None:
    # A word offered and not in the domain is a guaranteed refusal; one missing is an answer the
    # model cannot give.
    spec = convert_pydantic_to_tool_spec(CallSummaryAnswer)
    schema = spec["inputSchema"]["json"]
    properties = schema["properties"]
    detail = properties["details"]["items"]["properties"]

    assert spec["name"] == ANSWER_TOOL
    assert properties["intent"]["enum"] == [intent.value for intent in CallIntent]
    assert properties["outcome"]["enum"] == [outcome.value for outcome in CallOutcome]
    assert detail["kind"]["enum"] == [kind.value for kind in DetailKind]
    assert properties["headline"]["maxLength"] == MAX_HEADLINE_CHARACTERS
    assert properties["details"]["maxItems"] == MAX_DETAILS
    assert set(schema["required"]) == {"headline", "intent", "outcome"}


def test_an_answer_becomes_the_draft_it_describes() -> None:
    answer = CallSummaryAnswer.model_validate(
        {
            "headline": "Swift Parcels called, and your assistant noted it.",
            "intent": "delivery_in_progress",
            "outcome": "resolved_by_agent",
            "details": [
                {"kind": "name", "value": "Swift Parcels", "evidence": "it's Swift Parcels"}
            ],
        }
    )

    assert answer.to_draft() == SummaryDraft(
        headline="Swift Parcels called, and your assistant noted it.",
        intent=CallIntent.DELIVERY_IN_PROGRESS,
        outcome=CallOutcome.RESOLVED_BY_AGENT,
        details=(DraftDetail(DetailKind.NAME, "Swift Parcels", "it's Swift Parcels"),),
    )
