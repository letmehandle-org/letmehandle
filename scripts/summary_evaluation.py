# ruff: noqa: T201 - a terminal tool whose output is the point
"""Run the summary evaluation set against the configured model; not part of CI."""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from evaluation_runs import arguments, run_all, short_of

if TYPE_CHECKING:
    from letmehandle.application.calls.summariser import CallSummariser
    from tests.evaluation.summary_suite import SummaryScenario


async def evaluate(runs: int, minimum: float | None) -> int:
    """Summarise every call with the summariser the composition root builds, `runs` times."""
    from letmehandle.application.calls.prompts import SUMMARY_PROMPT_VERSION
    from letmehandle.application.calls.summariser import SUMMARY_WRITTEN, Written
    from letmehandle.application.orchestration.ports import Bounds
    from letmehandle.bootstrap import build_call_summariser
    from letmehandle.config.settings import ConfigurationError, get_settings
    from letmehandle.observability.logging import configure_logging
    from tests.evaluation.summary_suite import (
        SummaryReport,
        load_summary_scenarios,
        run_summaries,
    )
    from tests.support.recording_metrics import RecordingMetrics

    try:
        settings = get_settings()
        endpoint = settings.require_llm()
    except ConfigurationError as error:
        print(error, file=sys.stderr)
        return 2
    configure_logging(settings)
    metrics = RecordingMetrics()
    summariser = build_call_summariser(settings, timeout=Bounds().summary, metrics=metrics)

    def summariser_for(_scenario: SummaryScenario) -> CallSummariser:
        return summariser

    print(f"model {endpoint.model}, summary prompts {SUMMARY_PROMPT_VERSION}")
    scenarios = load_summary_scenarios()

    async def run_once() -> SummaryReport:
        return await run_summaries(scenarios, summariser_for)

    estimates = await run_all(runs, run_once)
    written = ", ".join(
        f"{each.value} {metrics.counted(SUMMARY_WRITTEN, outcome=each.value)}" for each in Written
    )
    print(f"\nwritten, over every run, from {written}")
    return short_of(estimates, minimum)


def main() -> None:
    parsed = arguments(__doc__.splitlines()[0], "set")
    sys.exit(asyncio.run(evaluate(parsed.runs, parsed.minimum)))


if __name__ == "__main__":
    main()
