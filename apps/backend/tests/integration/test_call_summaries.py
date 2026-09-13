"""Calls summarised end to end: the SDK loop, a scripted model, the real checks and fallback.

Nothing between the model's words and the summary is replaced. The SDK presents the answer's schema
as a tool, validates what comes back and tells the model why it was refused; the adapter turns the
answer into a draft; the summariser checks it against the call and keeps it or falls back. Only what
the model says is fixed.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.agent.strands.summary import ANSWER_TOOL
from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.bootstrap import call_summariser_on
from letmehandle.domain.models.call import Speaker
from letmehandle.domain.models.intent import CallIntent
from letmehandle.domain.models.summary import CallOutcome, ExtractedDetail
from tests.support.ended_calls import Ending, ended
from tests.support.scripted_model import (
    CallTool,
    CutOff,
    Fail,
    Hang,
    Say,
    ScriptedModel,
    write_summary,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.domain.models.summary import CallSummary
    from tests.support.scripted_model import Step

TIMEOUT = timedelta(seconds=5)

SAID = (
    (Speaker.CALLER, "Hello, it's Harbour Bank about the disputed charge of forty two pounds."),
    (Speaker.CALLER, "We've agreed to refund it, and the case number is HB 7731."),
    (Speaker.AGENT, "Thank you, I'll let him know."),
)
HEADLINE = (
    "Harbour Bank agreed to refund the forty two pound charge, and your assistant noted the case."
)
DETAILS = [
    {"kind": "amount", "value": "forty two pounds", "evidence": "charge of forty two pounds"},
    {"kind": "reference_number", "value": "HB 7731", "evidence": "the case number is HB 7731"},
    {"kind": "commitment_made", "value": "refund it", "evidence": "We've agreed to refund it"},
]
GOOD = write_summary(HEADLINE, intent="service_issue", details=DETAILS)


async def summarised(
    steps: Sequence[Step], facts: CallFacts | None = None, *, bound: timedelta = TIMEOUT
) -> tuple[CallSummary, ScriptedModel]:
    model = ScriptedModel(steps)
    facts = facts or ended(SAID)
    summary = await call_summariser_on(model, timeout=bound).summarise(
        facts, facts.call.transcript, locale="en"
    )
    return summary, model


def the_fallback(facts: CallFacts | None = None) -> CallSummary:
    return fallback_summary(facts or ended(SAID), locale="en")


class TestAModelThatAnswers:
    async def test_a_good_answer_becomes_the_summary_with_its_details(self) -> None:
        summary, model = await summarised([GOOD])

        assert summary.headline == HEADLINE
        assert summary.intent is CallIntent.SERVICE_ISSUE
        assert summary.outcome is CallOutcome.RESOLVED_BY_AGENT
        assert summary.details == (
            ExtractedDetail("amount", "forty two pounds", "charge of forty two pounds"),
            ExtractedDetail("reference_number", "HB 7731", "the case number is HB 7731"),
            ExtractedDetail("commitment_made", "refund it", "We've agreed to refund it"),
        )
        assert model.unused_steps == 0

    async def test_the_model_is_offered_only_the_answer_and_the_call_only_as_data(self) -> None:
        _, model = await summarised([GOOD])

        [request] = model.requests
        assert request.tool_names == (ANSWER_TOOL,)
        assert request.system_prompt is not None
        assert "HB 7731" not in request.system_prompt
        [message] = request.messages
        spoken = json.dumps(message)
        assert "HB 7731" in spoken
        assert "resolved_by_agent" in spoken

    async def test_a_handed_over_call_is_summarised_with_the_ending_it_had(self) -> None:
        facts = ended(SAID, Ending.HANDED_OVER)
        answer = write_summary(
            "Harbour Bank called about a refund, and your assistant handed it to you.",
            outcome="handed_to_user",
            intent="service_issue",
        )

        summary, _ = await summarised([answer], facts)

        assert summary.outcome is CallOutcome.HANDED_TO_USER
        assert summary.human_joined_at is not None
        assert summary.human_joined_at == the_fallback(facts).human_joined_at
        assert summary.details == ()

    async def test_an_invalid_answer_corrected_on_the_next_turn_is_kept(self) -> None:
        # The SDK tells the model why its answer was refused, and a model that fixes it answered.
        wrong = write_summary(HEADLINE, intent="refund_request", details=DETAILS)

        summary, model = await summarised([wrong, GOOD])

        assert summary.headline == HEADLINE
        assert model.unused_steps == 0


class TestAModelThatMisbehaves:
    @pytest.mark.parametrize(
        "steps",
        [
            pytest.param(
                [write_summary(HEADLINE, intent="refunds")] * 9, id="an intent outside the set"
            ),
            pytest.param(
                [write_summary(HEADLINE, outcome="Resolved")] * 9, id="an outcome outside the set"
            ),
            pytest.param(
                [write_summary(HEADLINE, details=[{**DETAILS[0], "kind": "phone"}])] * 9,
                id="a kind of detail outside the set",
            ),
            pytest.param(
                [write_summary(HEADLINE, details=[{"kind": "amount", "value": "42"}])] * 9,
                id="a detail with no evidence",
            ),
            pytest.param([write_summary("   ")] * 9, id="a headline with nothing in it"),
            pytest.param([write_summary("x" * 281)] * 9, id="a headline past the length"),
            pytest.param(
                [CallTool(ANSWER_TOOL, {"headline": HEADLINE, "intent": "service_issue"})] * 9,
                id="no outcome",
            ),
            pytest.param(
                [
                    CallTool(
                        ANSWER_TOOL,
                        {
                            "headline": HEADLINE,
                            "intent": "service_issue",
                            "outcome": "resolved_by_agent",
                            "importance": "urgent",
                        },
                    )
                ]
                * 9,
                id="a field nobody reads",
            ),
            pytest.param(
                [CallTool(ANSWER_TOOL, raw='{"headline": "Harbour Bank agre')] * 9,
                id="truncated JSON",
            ),
            pytest.param([Say("Harbour Bank called."), Say("Really.")], id="prose, twice"),
            pytest.param([CutOff('{"headline": "Harb')], id="cut off by the token limit"),
            pytest.param([Fail(ConnectionError("unreachable"))], id="an unreachable model"),
        ],
    )
    async def test_no_usable_answer_is_the_fallback(self, steps: list[Step]) -> None:
        summary, _ = await summarised(steps)

        assert summary == the_fallback()

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(
                write_summary(
                    HEADLINE,
                    details=[
                        {
                            "kind": "reference_number",
                            "value": "HB 7713",
                            "evidence": "the case number is HB 7713",
                        }
                    ],
                ),
                id="a reference number nobody read out",
            ),
            pytest.param(
                write_summary(
                    "Harbour Bank agreed to refund 42 pounds, and your assistant noted the case."
                ),
                id="a number in the headline nobody said",
            ),
            pytest.param(
                write_summary(HEADLINE, outcome="caller_hung_up"),
                id="an ending the call did not have",
            ),
            pytest.param(
                write_summary(
                    "Hello, it's Harbour Bank about the disputed charge of forty two pounds; "
                    "your assistant noted it."
                ),
                id="the call copied out",
            ),
        ],
    )
    async def test_a_well_formed_answer_the_checks_refuse_is_the_fallback(
        self, answer: CallTool
    ) -> None:
        summary, _ = await summarised([answer])

        assert summary == the_fallback()

    async def test_a_model_that_never_answers_is_abandoned_within_the_bound(self) -> None:
        before = asyncio.all_tasks()
        started = time.monotonic()

        summary, _ = await summarised([Hang()], bound=timedelta(seconds=0.2))

        assert time.monotonic() - started < 2
        assert summary == the_fallback()
        assert asyncio.all_tasks() == before

    async def test_a_model_that_keeps_answering_wrongly_is_stopped(self) -> None:
        summary, model = await summarised([write_summary(HEADLINE, intent="refunds")] * 50)

        assert summary == the_fallback()
        assert model.unused_steps > 0
