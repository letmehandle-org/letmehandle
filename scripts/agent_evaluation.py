# ruff: noqa: T201 - a terminal tool whose output is the point
"""Run the agent evaluation suite against the configured model; not part of CI."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from evaluation_runs import arguments, run_all, short_of

if TYPE_CHECKING:
    from letmehandle.application.agent.ports import CallActions, CallAgent
    from tests.evaluation.suite import Scenario


async def evaluate(runs: int, minimum: float | None) -> int:
    """Judge every scenario with the agent the composition root builds, `runs` times."""
    from letmehandle.application.agent.prompts import PROMPT_VERSION
    from letmehandle.bootstrap import build_call_judging
    from letmehandle.config.settings import ConfigurationError, get_settings
    from letmehandle.observability.logging import configure_logging
    from tests.evaluation.suite import Report, load_scenarios, run

    try:
        settings = get_settings()
        endpoint = settings.require_llm()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    configure_logging(settings)

    def agent_for(_scenario: Scenario, actions: CallActions) -> CallAgent:
        return build_call_judging(settings, actions=actions).agent

    print(f"model {endpoint.model}, prompts {PROMPT_VERSION}")
    scenarios = load_scenarios()

    async def run_once() -> Report:
        return await run(scenarios, agent_for)

    return short_of(await run_all(runs, run_once), minimum)


def main() -> None:
    parsed = arguments(__doc__.splitlines()[0], "suite")
    sys.exit(asyncio.run(evaluate(parsed.runs, parsed.minimum)))


if __name__ == "__main__":
    main()
