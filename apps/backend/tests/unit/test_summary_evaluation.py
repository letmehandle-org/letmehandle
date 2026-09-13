"""The summary evaluation set's scoring, proven with scripted models rather than a real one.

The set runs against a configured endpoint and is not part of the test run. What is proven here is
that it can be passed, by the reference summaries it was written with, and that it cannot be passed
by a model that writes the same summary for every call, copies the call out, keeps everything, or
is not there at all.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from letmehandle.adapters.agent.strands.summary import ANSWER_TOOL, CallSummaryAnswer
from letmehandle.application.agent.tools.outcome import MAX_DETAILS
from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summary_checks import problems_with
from letmehandle.application.calls.summary_draft import DetailKind, SummaryRequest
from letmehandle.bootstrap import call_summariser_on
from tests.evaluation.estimates import across_runs, below
from tests.evaluation.summary_suite import LOCALE, load_summary_scenarios, run_summaries
from tests.support.ended_calls import Ending
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_model import CallTool, Fail, ScriptedModel, write_summary

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from letmehandle.application.calls.summariser import CallSummariser
    from tests.evaluation.summary_suite import SummariserFor, SummaryScenario
    from tests.support.scripted_model import Step

CLASSES = ("extraction", "absent_detail", "ending", "no_details")
# Below this, one miss moves a class's rate by more than a prompt change is expected to, and a few
# runs cannot tell the two apart.
SMALLEST_CLASS = 12


def scripted(steps: Callable[[SummaryScenario], Sequence[Step]]) -> SummariserFor:
    """A summariser whose model answers each scenario with `steps` of it."""

    def summariser_for(scenario: SummaryScenario) -> CallSummariser:
        return call_summariser_on(
            ScriptedModel(steps(scenario)),
            timeout=timedelta(seconds=5),
            metrics=RecordingMetrics(),
        )

    return summariser_for


def outcome_of(scenario: SummaryScenario) -> str:
    return fallback_summary(scenario.facts(), locale=LOCALE).outcome.value


def reference_answer(scenario: SummaryScenario, **changes: object) -> dict[str, object]:
    """The answer a model gives when it writes the scenario's reference summary."""
    answer: dict[str, object] = {
        "headline": scenario.reference.headline,
        "intent": scenario.intents[0].value,
        "outcome": outcome_of(scenario),
        "details": [detail.model_dump(mode="json") for detail in scenario.reference.details],
    }
    answer.update(changes)
    return answer


def the_reference(scenario: SummaryScenario) -> list[Step]:
    return [CallTool(ANSWER_TOOL, reference_answer(scenario))]


async def test_the_reference_summaries_pass_every_class() -> None:
    # Otherwise the set asks for something the checks refuse, and no model could reach it.
    report = await run_summaries(load_summary_scenarios(), scripted(the_reference))

    assert [o.misses for o in report.outcomes if not o.passed] == []
    assert below(across_runs([report.pass_rates()]), 1.0) == []


def test_every_reference_summary_passes_the_checks() -> None:
    for scenario in load_summary_scenarios():
        facts = scenario.facts()
        request = SummaryRequest(
            known=fallback_summary(facts, locale=LOCALE),
            transcript=facts.call.transcript,
            locale=LOCALE,
        )
        draft = CallSummaryAnswer.model_validate(reference_answer(scenario)).to_draft()
        assert problems_with(draft, request) == (), scenario.id


def test_the_set_covers_every_class_and_every_kind_of_detail() -> None:
    scenarios = load_summary_scenarios()
    for name in CLASSES:
        assert len([s for s in scenarios if s.summary_class == name]) >= SMALLEST_CLASS, name
    extracted = {detail.kind for s in scenarios for detail in s.reference.details}
    assert extracted == set(DetailKind)
    # Every ending a model is asked to name, not only the calls the assistant resolved.
    assert {s.ending for s in scenarios} == set(Ending)


def same_for_every_call(scenario: SummaryScenario) -> list[Step]:
    del scenario
    return [write_summary("Your assistant took the call.", intent="enquiry")]


def copies_the_call_out(scenario: SummaryScenario) -> list[Step]:
    longest = max((text for _, text in scenario.said), key=len)
    return [write_summary(f"{longest} Your assistant took it.", outcome=outcome_of(scenario))]


def keeps_everything(scenario: SummaryScenario) -> list[Step]:
    # The reference, with the longest line of the call kept as a detail of every kind: grounded,
    # and useless.
    longest = max((text for _, text in scenario.said), key=len)
    everything = [
        {"kind": kind.value, "value": longest, "evidence": longest} for kind in DetailKind
    ]
    return [CallTool(ANSWER_TOOL, reference_answer(scenario, details=everything[:MAX_DETAILS]))]


def is_down(scenario: SummaryScenario) -> list[Step]:
    del scenario
    return [Fail(RuntimeError("model down"))]


@pytest.mark.parametrize(
    "strategy",
    [
        pytest.param(same_for_every_call, id="the same summary for every call"),
        pytest.param(copies_the_call_out, id="copies the call out"),
        pytest.param(keeps_everything, id="keeps everything as a detail"),
        pytest.param(is_down, id="always the fallback"),
    ],
)
async def test_a_degenerate_model_fails_every_class(
    strategy: Callable[[SummaryScenario], Sequence[Step]],
) -> None:
    # A set a degenerate answer can pass in any class measures nothing in that class.
    report = await run_summaries(load_summary_scenarios(), scripted(strategy))

    assert sorted(below(across_runs([report.pass_rates()]), 1.0)) == sorted(CLASSES)


SCENARIO: dict[str, object] = {
    "id": "courier",
    "class": "extraction",
    "said": [["caller", "Swift Parcels here, it is at 14 Alder Close."]],
    "intents": ["delivery_in_progress"],
    "reference": {
        "headline": "Swift Parcels left it at 14 Alder Close, and your assistant noted it.",
        "details": [
            {"kind": "address", "value": "14 Alder Close", "evidence": "at 14 Alder Close"}
        ],
    },
    "absent": ["amount"],
}


def write(path: Path, scenarios: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps(scenarios))
    return path


async def test_each_miss_is_named(tmp_path: Path) -> None:
    wrong = write_summary(
        "Swift Parcels called, and your assistant noted it.",
        intent="sales",
        details=[{"kind": "amount", "value": "Swift Parcels", "evidence": "Swift Parcels here"}],
    )
    scenarios = load_summary_scenarios(write(tmp_path / "s.json", [SCENARIO]))

    report = await run_summaries(scenarios, scripted(lambda _: [wrong]))

    assert report.outcomes[0].misses == (
        "intent sales is not one of the expected",
        "no address detail carrying '14 Alder Close'",
        "a amount detail was kept, and the call contains none",
    )
    assert report.pass_rates() == {"extraction": (0, 1)}


async def test_a_fallback_is_a_miss_even_where_nothing_else_is(tmp_path: Path) -> None:
    quiet: dict[str, object] = {
        **SCENARIO,
        "intents": ["undetermined"],
        "reference": {"headline": "Your assistant took a call.", "details": []},
    }
    scenarios = load_summary_scenarios(write(tmp_path / "s.json", [quiet]))

    report = await run_summaries(scenarios, scripted(is_down))

    assert report.outcomes[0].misses == (
        "the model's summary was not kept; the fallback was written",
    )


def test_a_repeated_scenario_id_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="repeated: \\['courier'\\]"):
        load_summary_scenarios(write(tmp_path / "s.json", [SCENARIO, SCENARIO]))


def test_a_detail_both_expected_and_absent_is_refused(tmp_path: Path) -> None:
    contradictory: dict[str, object] = {**SCENARIO, "absent": ["address"]}
    with pytest.raises(ValueError, match="marked absent in: \\['courier'\\]"):
        load_summary_scenarios(write(tmp_path / "s.json", [contradictory]))


def test_an_expectation_nothing_checks_is_refused(tmp_path: Path) -> None:
    malformed: dict[str, object] = {**SCENARIO, "absnet": ["amount"]}
    with pytest.raises(ValidationError, match="absnet"):
        load_summary_scenarios(write(tmp_path / "s.json", [malformed]))


def test_a_reason_that_says_nothing_is_refused(tmp_path: Path) -> None:
    blank: dict[str, object] = {**SCENARIO, "why": ""}
    with pytest.raises(ValidationError, match="why"):
        load_summary_scenarios(write(tmp_path / "s.json", [blank]))
