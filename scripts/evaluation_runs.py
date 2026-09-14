# ruff: noqa: T201 - a terminal tool whose output is the point
"""The command line, run loop and report the agent and summary evaluation scripts share."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

BACKEND = Path(__file__).resolve().parents[1] / "apps" / "backend"

sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND))

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from tests.evaluation.estimates import Estimate


class _Scenario(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def why(self) -> str | None: ...


class _Outcome(Protocol):
    @property
    def scenario(self) -> _Scenario: ...

    @property
    def misses(self) -> Sequence[str]: ...

    @property
    def passed(self) -> bool: ...


class EvaluationReport[C: str](Protocol):
    """One run's outcomes and the pass rate of each class of call."""

    @property
    def outcomes(self) -> Sequence[_Outcome]: ...

    def pass_rates(self) -> Mapping[C, tuple[int, int]]: ...


def arguments(description: str, unit: str) -> argparse.Namespace:
    """`--runs` and `--minimum`, parsed from the command line."""
    from tests.evaluation.estimates import run_count

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--runs",
        type=run_count,
        default=1,
        help=f"how many times to run the whole {unit}, reported together",
    )
    parser.add_argument(
        "--minimum", type=float, help="the pass rate, as a fraction, every class must reach"
    )
    return parser.parse_args()


def print_report[C: str](report: EvaluationReport[C]) -> None:
    """Each scenario's outcome and misses, then each class's pass rate."""
    for outcome in report.outcomes:
        print(f"{'pass' if outcome.passed else 'FAIL'}  {outcome.scenario.id}")
        for miss in outcome.misses:
            print(f"        {miss}")
        if outcome.misses and outcome.scenario.why:
            print(f"        why: {outcome.scenario.why}")
    print()
    for name, (passed, total) in report.pass_rates().items():
        print(f"{name:<16} {passed}/{total}  {passed / total:.0%}")


async def run_all[C: str](
    runs: int, run_once: Callable[[], Awaitable[EvaluationReport[C]]]
) -> dict[C, Estimate]:
    """Run the set `runs` times, printing each report and the estimates across every run."""
    from tests.evaluation.estimates import across_runs, table

    rates = []
    for number in range(1, runs + 1):
        print(f"\nrun {number} of {runs}\n")
        report = await run_once()
        print_report(report)
        rates.append(report.pass_rates())
    estimates = across_runs(rates)
    print(f"\n{table(estimates, runs)}")
    return estimates


def short_of[C: str](estimates: Mapping[C, Estimate], minimum: float | None) -> int:
    """Exit status 1, naming the classes, when any class passed less often than `minimum`."""
    from tests.evaluation.estimates import below

    if minimum is not None and (short := below(estimates, minimum)):
        print(f"\nbelow {minimum:.0%}: {', '.join(short)}", file=sys.stderr)
        return 1
    return 0
