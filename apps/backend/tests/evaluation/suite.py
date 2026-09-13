"""The evaluation suite: fixed calls, expected decisions, a pass rate per class of call.

Deterministic tests prove the logic with a scripted model. This measures a real one — whether the
configured model, with the current prompts, reaches the decisions a person would expect — and it is
how a prompt or policy change shows it did not make the assistant worse. It is not part of the test
run: it needs a model endpoint, and its answers vary.

Scenarios are data, in `scenarios.json`. Each is a call, what the user has granted, and what
must be true of the judgement: whether the user is reached, whether their phone rings now, and
optionally why, which intents are acceptable, what the caller was asking the assistant to do, and
which actions must not have happened to the call. An expectation left out is not checked, so a
scenario says only what it means.

The tools are the real ones, from the registry, acting on a recording of the call, and the
conclusion acts through the real escalation service. What is evaluated is what will run.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from letmehandle.application.agent.ports import CallEnding
from letmehandle.domain.models.authority import AgentAuthority, Capability

# Read by pydantic when it builds the scenario models, so they are needed at run time.
from letmehandle.domain.models.escalation import EscalationReason  # noqa: TC001
from letmehandle.domain.models.intent import CallIntent  # noqa: TC001
from tests.support.agent_calls import a_call
from tests.support.recording_call_actions import (
    Ended,
    Escalated,
    MessageTaken,
    Recorded,
    RecordingCallActions,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from letmehandle.application.agent.ports import AgentJudgement, CallActions, CallAgent
    from tests.support.recording_call_actions import Action

SCENARIOS: Final = Path(__file__).with_name("scenarios.json")

type ScenarioClass = Literal["routine", "escalation", "unsafe_request", "suspected_fraud"]

# What can happen to a call, by the name of the `CallActions` method that does it — except that an
# ending is named by what it does to the caller. `end_call` is a hang-up, resolved or declined.
# `hand_over` is the assistant stepping back for a user it has just reached: orchestration keeps the
# caller company until the user answers and takes the call back if they do not, so a scenario that
# forbids hanging up on a caller has not been broken by one.
type ActionName = Literal["escalate", "record_outcome", "take_message", "end_call", "hand_over"]

ACTION_NAMES: Final[Mapping[type[object], ActionName]] = {
    Escalated: "escalate",
    Recorded: "record_outcome",
    MessageTaken: "take_message",
}


def action_name(action: Action) -> ActionName:
    """The name a scenario forbids `action` by."""
    if isinstance(action, Ended):
        return "hand_over" if action.ending is CallEnding.HANDED_OVER else "end_call"
    return ACTION_NAMES[type(action)]


class Expectation(BaseModel):
    """What must be true of the judgement. Anything left out is not checked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    escalates: bool | None = None
    rings_now: bool | None = None
    reason: EscalationReason | None = None
    intents: tuple[CallIntent, ...] = ()
    requested_capability: Capability | None = None
    forbidden_actions: tuple[ActionName, ...] = ()


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


def misses(
    scenario: Scenario, judgement: AgentJudgement, actions: RecordingCallActions
) -> list[str]:
    """Every way the judgement differs from what the scenario expects. Empty is a pass."""
    expect = scenario.expect
    found: list[str] = []
    escalation = judgement.escalation
    if expect.escalates is not None and escalation.required is not expect.escalates:
        found.append(f"expected escalates={expect.escalates}, got {escalation.required}")
    if expect.rings_now is not None and escalation.is_immediate is not expect.rings_now:
        found.append(f"expected rings_now={expect.rings_now}, got {escalation.is_immediate}")
    if expect.reason is not None and escalation.reason is not expect.reason:
        found.append(f"expected reason {expect.reason.value}, got {escalation.reason}")
    if expect.intents and judgement.proposal.intent not in expect.intents:
        found.append(f"intent {judgement.proposal.intent.value} is not one of the expected")
    requested = judgement.proposal.requested_capability
    if expect.requested_capability is not None and requested is not expect.requested_capability:
        found.append(f"expected a request to {expect.requested_capability.value}, got {requested}")
    acted = {action_name(action) for action in actions.actions}
    found.extend(
        f"{name} happened, and must not have" for name in expect.forbidden_actions if name in acted
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


# Given the call's actions, because the agent wires its tools and its escalation check around them.
type AgentFor = Callable[[Scenario, CallActions], CallAgent]


async def run(scenarios: Sequence[Scenario], agent_for: AgentFor) -> Report:
    """Judge every scenario with an agent built for it, one at a time."""
    outcomes: list[Outcome] = []
    for scenario in scenarios:
        # Fresh for each scenario, so what one call did is never read as another's.
        actions = RecordingCallActions()
        call = a_call(
            *scenario.said,
            authority=AgentAuthority.granting(*scenario.granted),
            from_important_contact=scenario.from_important_contact,
        )
        judgement = await agent_for(scenario, actions).judge(call)
        outcomes.append(Outcome(scenario, tuple(misses(scenario, judgement, actions))))
    return Report(tuple(outcomes))
