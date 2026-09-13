"""The parts of the agent that are not a scenario: its bound, its schema, and what it logs."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
import structlog
from pydantic import ValidationError
from strands.tools import convert_pydantic_to_tool_spec
from structlog.testing import capture_logs

from letmehandle.adapters.agent.strands.agent import ASSESSMENT_TOOL, StrandsCallAgent
from letmehandle.adapters.agent.strands.assessment import CallAssessment
from letmehandle.application.agent.conclusion import JudgementConclusion
from letmehandle.application.agent.escalation import EscalationService
from letmehandle.application.agent.tools.arguments import SHORT_TEXT_CHARACTERS
from letmehandle.domain.models.authority import Capability
from letmehandle.domain.models.intent import CallImportance, CallIntent
from tests.support.agent_calls import a_call, fixed
from tests.support.recording_call_actions import RecordingCallActions
from tests.support.scripted_model import Fail, Say, ScriptedModel, Step

if TYPE_CHECKING:
    from collections.abc import Iterator


def a_conclusion() -> JudgementConclusion:
    actions = RecordingCallActions()
    return JudgementConclusion(actions, EscalationService(actions))


@pytest.fixture
def unfiltered_logging() -> Iterator[None]:
    # Another test may have configured logging at a level that drops these events before they
    # could be captured. Whatever was configured is put back afterwards.
    configured = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**configured)


@pytest.mark.parametrize("seconds", [0, -1])
def test_a_judgement_with_no_time_to_happen_in_is_refused(seconds: float) -> None:
    with pytest.raises(ValueError, match="time"):
        StrandsCallAgent(
            ScriptedModel([]),
            tools=fixed(),
            conclusion=a_conclusion(),
            timeout=timedelta(seconds=seconds),
        )


@pytest.mark.usefixtures("unfiltered_logging")
@pytest.mark.parametrize(
    "steps", [[Fail(RuntimeError("the caller said: my card is 4111"))], [Say("no")] * 2]
)
async def test_a_failure_is_logged_by_kind_and_never_by_content(steps: list[Step]) -> None:
    secret = "my card is 4111"
    agent = StrandsCallAgent(
        ScriptedModel(steps),
        tools=fixed(),
        conclusion=a_conclusion(),
        timeout=timedelta(seconds=5),
    )
    with capture_logs() as events:
        await agent.judge(a_call(secret))

    assert events
    assert all(event["call_id"] == "call-1" for event in events)
    assert secret not in repr(events)


def test_the_schema_offers_exactly_the_words_the_domain_has() -> None:
    # A value added to the domain and not offered to the model is a judgement it cannot make; one
    # offered and not in the domain is a guaranteed refusal.
    spec = convert_pydantic_to_tool_spec(CallAssessment)
    properties = spec["inputSchema"]["json"]["properties"]
    assert spec["name"] == ASSESSMENT_TOOL
    assert properties["intent"]["enum"] == [intent.value for intent in CallIntent]
    assert properties["importance"]["enum"] == [level.name.lower() for level in CallImportance]
    assert properties["requested_capability"]["enum"] == [each.value for each in Capability]


def test_an_importance_is_written_back_as_the_word_it_was_given_as() -> None:
    assessment = CallAssessment.model_validate(
        {
            "intent": "sales",
            "importance": "low",
            "understood": True,
            "caller_asked_for_the_user": False,
            "needs_the_users_decision": False,
            "requested_capability": None,
            "caller_summary": None,
        }
    )
    assert assessment.importance is CallImportance.LOW
    assert assessment.model_dump(mode="json")["importance"] == "low"


def test_a_caller_summary_is_held_to_the_escalation_tools_limit() -> None:
    fields = {
        "intent": "sales",
        "importance": "low",
        "understood": True,
        "caller_asked_for_the_user": False,
        "needs_the_users_decision": False,
    }
    CallAssessment.model_validate({**fields, "caller_summary": "a" * SHORT_TEXT_CHARACTERS})

    with pytest.raises(ValidationError, match="caller_summary"):
        CallAssessment.model_validate(
            {**fields, "caller_summary": "a" * (SHORT_TEXT_CHARACTERS + 1)}
        )
