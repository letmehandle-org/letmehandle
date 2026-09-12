# ruff: noqa: T201 - a terminal tool whose output is the point
"""Run the agent evaluation suite against the configured model.

Every scenario in `apps/backend/tests/evaluation/scenarios.json` is judged by the agent exactly as
the composition root builds it — the configured endpoint, the current prompts, the real escalation
policy — and the pass rate of each class of call is printed. Not part of the test run or CI: it
needs a model endpoint, and a real model's answers vary.

    cd apps/backend
    uv run python ../../scripts/agent_evaluation.py
    uv run python ../../scripts/agent_evaluation.py --minimum 0.9

With `--minimum`, it exits non-zero when any class passes less often than that fraction, so a
prompt change can be held to the rate the last one reached. The model's key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"

# The backend's sources and its test support, which holds the suite and the stand-in tools.
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.application.agent.ports import CallAgent
    from letmehandle.application.agent.tool import AgentTool
    from tests.evaluation.suite import Report, Scenario


def print_report(report: Report) -> None:
    for outcome in report.outcomes:
        mark = "pass" if outcome.passed else "FAIL"
        print(f"{mark}  {outcome.scenario.id}")
        for miss in outcome.misses:
            print(f"        {miss}")
    print()
    for name, (passed, total) in report.pass_rates().items():
        print(f"{name:<16} {passed}/{total}  {passed / total:.0%}")


async def evaluate(minimum: float | None) -> int:
    # Imported after the path is set.
    from letmehandle.application.agent.prompts import PROMPT_VERSION
    from letmehandle.bootstrap import build_call_agent
    from letmehandle.config.settings import ConfigurationError, get_settings
    from letmehandle.observability.logging import configure_logging
    from tests.evaluation.suite import load_scenarios, run
    from tests.support.agent_calls import decide_by_policy

    try:
        settings = get_settings()
        endpoint = settings.require_llm()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    # To stderr, at the configured level, so why a scenario fell back is beside the report and not
    # inside it.
    configure_logging(settings)

    def agent_for(_scenario: Scenario, tools: Sequence[AgentTool]) -> CallAgent:
        return build_call_agent(settings, tools=tools, consider=decide_by_policy)

    print(f"model {endpoint.model}, prompts {PROMPT_VERSION}\n")
    report = await run(load_scenarios(), agent_for)
    print_report(report)

    if minimum is not None and (short := report.below(minimum)):
        print(f"\nbelow {minimum:.0%}: {', '.join(short)}", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--minimum",
        type=float,
        help="the pass rate, as a fraction, every class must reach",
    )
    arguments = parser.parse_args()
    sys.exit(asyncio.run(evaluate(arguments.minimum)))


if __name__ == "__main__":
    main()
