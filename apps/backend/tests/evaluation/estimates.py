"""A class's pass rate pooled over several runs, with its 95% Wilson interval."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The normal quantile for a two-sided 95% interval.
Z_95: Final = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Estimate:
    """How often a class passed over every run, and the 95% interval around that rate."""

    passed: int
    total: int

    @property
    def mean(self) -> float:
        return self.passed / self.total

    @property
    def interval(self) -> tuple[float, float]:
        """The Wilson score interval at 95%, as fractions."""
        n = self.total
        centre = self.mean + Z_95**2 / (2 * n)
        spread = Z_95 * math.sqrt(self.mean * (1 - self.mean) / n + Z_95**2 / (4 * n**2))
        scale = 1 + Z_95**2 / n
        return max(0.0, (centre - spread) / scale), min(1.0, (centre + spread) / scale)


def across_runs[C: str](runs: Sequence[Mapping[C, tuple[int, int]]]) -> dict[C, Estimate]:
    """Each class's passed and total summed over `runs`, in the order classes first appear."""
    if not runs:
        raise ValueError("there is no estimate from no runs")
    pooled: dict[C, Estimate] = {}
    for rates in runs:
        for name, (passed, total) in rates.items():
            before = pooled.get(name, Estimate(0, 0))
            pooled[name] = Estimate(before.passed + passed, before.total + total)
    return pooled


def below[C: str](estimates: Mapping[C, Estimate], minimum: float) -> list[C]:
    """The classes whose rate over every run is under `minimum`, a fraction."""
    return [name for name, each in estimates.items() if each.passed < minimum * each.total]


def table[C: str](estimates: Mapping[C, Estimate], runs: int) -> str:
    """The estimates as a report prints them: passed of total, the mean, and its interval."""
    lines = [f"over {runs} run{'s' if runs != 1 else ''}, 95% interval"]
    for name, each in estimates.items():
        low, high = each.interval
        lines.append(
            f"{name:<16} {each.passed}/{each.total}  {each.mean:.0%}  [{low:.0%}, {high:.0%}]"
        )
    return "\n".join(lines)


def run_count(text: str) -> int:
    """A number of runs read from the command line, which is at least one."""
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError("a number of runs is at least 1")
    return count
