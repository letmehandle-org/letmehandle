"""The summary evaluation set: fixed calls, expected extractions, a pass rate per class of call.

Deterministic tests prove the checks and the fallback with a scripted model. This measures a real
one: whether the configured model, with the current summary prompts, writes summaries the checks
keep and that carry what the call contained. A prompt change that degrades summaries shows here.
It is not part of the test run, because it needs a model endpoint and its answers vary.

Calls are data, in `summaries.json`. Each is what was said, how the call ended, which intents are
acceptable, a reference summary a person wrote, and the kinds of detail the call does not contain
however tempting they look. A summary passes when a model's draft was kept rather than the
fallback, its intent is acceptable, it carries every reference detail, and it carries no detail of
an absent kind. The reference summary is itself held to the checks, so the set cannot ask for a
summary the product would refuse.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summary_checks import words

# Read by pydantic when it builds the scenario models, so they are needed at run time.
from letmehandle.application.calls.summary_draft import DetailKind  # noqa: TC001
from letmehandle.domain.models.call import Speaker  # noqa: TC001
from letmehandle.domain.models.intent import CallIntent  # noqa: TC001
from tests.support.ended_calls import Ending, ended

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.domain.models.summary import CallSummary

SUMMARIES: Final = Path(__file__).with_name("summaries.json")
LOCALE: Final = "en"

type SummaryClass = Literal["extraction", "absent_detail", "ending", "no_details"]


class ReferenceDetail(BaseModel):
    """A detail the call contains, written the way the prompt asks for it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DetailKind
    value: str
    evidence: str


class Reference(BaseModel):
    """The summary a person would keep for the call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: str
    details: tuple[ReferenceDetail, ...]


class SummaryScenario(BaseModel):
    """One ended call and what its summary must carry."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    summary_class: SummaryClass = Field(alias="class")
    ending: Ending = Ending.RESOLVED
    said: tuple[tuple[Speaker, str], ...] = Field(min_length=1)
    intents: tuple[CallIntent, ...] = Field(min_length=1)
    reference: Reference
    absent: tuple[DetailKind, ...] = Field(min_length=1)

    def facts(self) -> CallFacts:
        """The call as orchestration hands it over at teardown."""
        return ended(self.said, self.ending)


def load_summary_scenarios(path: Path = SUMMARIES) -> tuple[SummaryScenario, ...]:
    """Every scenario in the file, refused whole if one is malformed, repeated or contradictory."""
    scenarios = tuple(SummaryScenario.model_validate(each) for each in json.loads(path.read_text()))
    repeated = sorted(name for name, seen in Counter(s.id for s in scenarios).items() if seen > 1)
    if repeated:
        raise ValueError(f"scenario ids must be unique; repeated: {repeated}")
    contradictory = sorted(
        scenario.id
        for scenario in scenarios
        if {detail.kind for detail in scenario.reference.details} & set(scenario.absent)
    )
    if contradictory:
        raise ValueError(f"a reference detail is of a kind marked absent in: {contradictory}")
    return scenarios


def misses(scenario: SummaryScenario, summary: CallSummary) -> list[str]:
    """Every way the summary falls short of the scenario. Empty is a pass."""
    found: list[str] = []
    if summary == fallback_summary(scenario.facts(), locale=LOCALE):
        found.append("the model's summary was not kept; the fallback was written")
    if summary.intent not in scenario.intents:
        found.append(f"intent {summary.intent.value} is not one of the expected")
    for expected in scenario.reference.details:
        wanted = set(words(expected.value))
        if not any(
            detail.label == expected.kind.value and wanted <= set(words(detail.value))
            for detail in summary.details
        ):
            found.append(f"no {expected.kind.value} detail carrying {expected.value!r}")
    kept = {detail.label for detail in summary.details}
    found.extend(
        f"a {kind.value} detail was kept, and the call contains none"
        for kind in scenario.absent
        if kind.value in kept
    )
    return found


@dataclass(frozen=True, slots=True)
class SummaryOutcome:
    scenario: SummaryScenario
    misses: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.misses


@dataclass(frozen=True, slots=True)
class SummaryReport:
    """The result of one run: every outcome, and the pass rate of each class of call."""

    outcomes: tuple[SummaryOutcome, ...]

    def pass_rates(self) -> dict[SummaryClass, tuple[int, int]]:
        """Passed and total, per class, in the order classes first appear."""
        rates: dict[SummaryClass, tuple[int, int]] = {}
        for outcome in self.outcomes:
            passed, total = rates.get(outcome.scenario.summary_class, (0, 0))
            rates[outcome.scenario.summary_class] = (passed + int(outcome.passed), total + 1)
        return rates


type SummariserFor = Callable[[SummaryScenario], CallSummariser]


async def run_summaries(
    scenarios: Sequence[SummaryScenario], summariser_for: SummariserFor
) -> SummaryReport:
    """Summarise every scenario with a summariser built for it, one at a time."""
    outcomes: list[SummaryOutcome] = []
    for scenario in scenarios:
        facts = scenario.facts()
        summary = await summariser_for(scenario).summarise(
            facts, facts.call.transcript, locale=LOCALE
        )
        outcomes.append(SummaryOutcome(scenario, tuple(misses(scenario, summary))))
    return SummaryReport(tuple(outcomes))
