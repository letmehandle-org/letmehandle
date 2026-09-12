"""The evaluation suite: fixed calls, expected decisions, a pass rate per class of call.

Deterministic tests prove the logic with a scripted model. This measures a real one — whether the
configured model, with the current prompts, reaches the decisions a person would expect — and it is
how a prompt or policy change shows it did not make the assistant worse. It is not part of the test
run: it needs a model endpoint, and its answers vary.

Scenarios are data, in `scenarios.json`. Each is a call, what the user has granted, and what
must be true of the judgement: whether the user is reached, and optionally why, which intents are
acceptable, and which actions must not have happened. An expectation left out is not checked, so a
scenario says only what it means.

The tools are stand-ins with the names and the grant check of the real ones. The suite measures the
model and the prompts; a tool's own behaviour is proven by its own tests.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from letmehandle.domain.models.authority import AgentAuthority, Capability

# Read by pydantic when it builds the scenario models, so they are needed at run time.
from letmehandle.domain.models.escalation import EscalationReason  # noqa: TC001
from letmehandle.domain.models.intent import CallIntent  # noqa: TC001
from tests.support.agent_calls import GuardedTool, a_call

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from letmehandle.application.agent.ports import AgentJudgement, CallAgent
    from letmehandle.application.agent.tool import AgentTool

SCENARIOS: Final = Path(__file__).with_name("scenarios.json")

type ScenarioClass = Literal["routine", "escalation", "unsafe_request", "suspected_fraud"]


class Expectation(BaseModel):
    """What must be true of the judgement. Anything left out is not checked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    escalates: bool | None = None
    reason: EscalationReason | None = None
    intents: tuple[CallIntent, ...] = ()
    forbidden_actions: tuple[str, ...] = ()


class Scenario(BaseModel):
    """One call, the user's rules for it, and the expected decision."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: str
    scenario_class: ScenarioClass = Field(alias="class")
    said: tuple[str, ...] = Field(min_length=1)
    granted: tuple[Capability, ...] = ()
    from_important_contact: bool = False
    expect: Expectation


def load_scenarios(path: Path = SCENARIOS) -> tuple[Scenario, ...]:
    """Every scenario in the file, refused whole if any one is malformed or an id repeats."""
    scenarios = tuple(Scenario.model_validate(each) for each in json.loads(path.read_text()))
    repeated = sorted(name for name, seen in Counter(s.id for s in scenarios).items() if seen > 1)
    if repeated:
        raise ValueError(f"scenario ids must be unique; repeated: {repeated}")
    return scenarios


def stand_in_tools() -> tuple[GuardedTool, ...]:
    """Fresh tools for one scenario, so what one call did is never read as another's."""
    return (
        GuardedTool(
            "request_human_escalation",
            description="Ask for the user to be reached. The user's rules decide whether they are.",
        ),
        GuardedTool(
            "take_message",
            Capability.TAKE_A_MESSAGE,
            description="Keep a message from the caller for the user.",
        ),
        GuardedTool(
            "share_delivery_instructions",
            Capability.SHARE_DELIVERY_INSTRUCTIONS,
            description="Tell a courier where the user wants parcels left.",
        ),
        GuardedTool(
            "reschedule_appointment",
            Capability.RESCHEDULE_APPOINTMENTS,
            description="Move one of the user's appointments to another time.",
        ),
        GuardedTool(
            "share_contact_details",
            Capability.SHARE_CONTACT_DETAILS,
            description="Pass the user's contact details to the caller.",
        ),
        GuardedTool("end_call", description="Hang up, with a reason."),
    )


def misses(
    scenario: Scenario, judgement: AgentJudgement, tools: Sequence[GuardedTool]
) -> list[str]:
    """Every way the judgement differs from what the scenario expects. Empty is a pass."""
    expect = scenario.expect
    found: list[str] = []
    escalation = judgement.escalation
    if expect.escalates is not None and escalation.required is not expect.escalates:
        found.append(f"expected escalates={expect.escalates}, got {escalation.required}")
    if expect.reason is not None and escalation.reason is not expect.reason:
        found.append(f"expected reason {expect.reason.value}, got {escalation.reason}")
    if expect.intents and judgement.proposal.intent not in expect.intents:
        found.append(f"intent {judgement.proposal.intent.value} is not one of the expected")
    acted = {tool.name for tool in tools if tool.acted}
    found.extend(
        f"{name} acted, and must not have" for name in expect.forbidden_actions if name in acted
    )
    return found


@dataclass(frozen=True, slots=True)
class Outcome:
    scenario: Scenario
    misses: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.misses


@dataclass(frozen=True, slots=True)
class Report:
    """The result of one run: every outcome, and the pass rate of each class of call."""

    outcomes: tuple[Outcome, ...]

    def pass_rates(self) -> dict[ScenarioClass, tuple[int, int]]:
        """Passed and total, per class, in the order classes first appear."""
        rates: dict[ScenarioClass, tuple[int, int]] = {}
        for outcome in self.outcomes:
            passed, total = rates.get(outcome.scenario.scenario_class, (0, 0))
            rates[outcome.scenario.scenario_class] = (passed + int(outcome.passed), total + 1)
        return rates

    def below(self, minimum: float) -> list[ScenarioClass]:
        """The classes whose pass rate is under `minimum`, a fraction."""
        return [
            name for name, (passed, total) in self.pass_rates().items() if passed < minimum * total
        ]


type AgentFor = Callable[[Scenario, Sequence[AgentTool]], CallAgent]


async def run(scenarios: Sequence[Scenario], agent_for: AgentFor) -> Report:
    """Judge every scenario with an agent built for it, one at a time."""
    outcomes: list[Outcome] = []
    for scenario in scenarios:
        tools = stand_in_tools()
        call = a_call(
            *scenario.said,
            authority=AgentAuthority.granting(*scenario.granted),
            from_important_contact=scenario.from_important_contact,
        )
        judgement = await agent_for(scenario, tools).judge(call)
        outcomes.append(Outcome(scenario, tuple(misses(scenario, judgement, tools))))
    return Report(tuple(outcomes))
