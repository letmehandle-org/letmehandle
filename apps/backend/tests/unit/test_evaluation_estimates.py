"""Pass rates pooled over several evaluation runs, and the interval a report states beside them."""

from __future__ import annotations

import argparse

import pytest

from tests.evaluation.estimates import Estimate, across_runs, below, run_count, table


@pytest.mark.parametrize(
    ("passed", "total", "low", "high"),
    [
        pytest.param(11, 12, 0.6461, 0.9851, id="one miss in twelve"),
        pytest.param(33, 36, 0.7817, 0.9713, id="three misses in thirty-six"),
        # The textbook interval would say [100%, 100%] here, which no finite sample shows.
        pytest.param(36, 36, 0.9036, 1.0, id="every call passed"),
        pytest.param(0, 10, 0.0, 0.2775, id="every call missed"),
    ],
)
def test_the_interval_is_wilsons_at_95_percent(
    passed: int, total: int, low: float, high: float
) -> None:
    assert Estimate(passed, total).interval == pytest.approx((low, high), abs=1e-4)


def test_more_runs_of_the_same_rate_narrow_the_interval() -> None:
    one = Estimate(11, 12).interval
    three = Estimate(33, 36).interval

    assert one[1] - one[0] > three[1] - three[0]


def test_runs_are_pooled_call_by_call_per_class() -> None:
    estimates = across_runs(
        [{"routine": (3, 4), "escalation": (5, 5)}, {"routine": (4, 4), "escalation": (4, 5)}]
    )

    assert estimates == {"routine": Estimate(7, 8), "escalation": Estimate(9, 10)}
    assert estimates["routine"].mean == 0.875


def test_the_minimum_is_held_to_the_pooled_rate_not_the_worst_run() -> None:
    # A run of 3 of 4 is 75%, but over both runs routine passed 7 of 8.
    estimates = across_runs([{"routine": (3, 4), "sales": (1, 4)}, {"routine": (4, 4)}])

    assert below(estimates, 0.85) == ["sales"]
    assert below(estimates, 0.9) == ["routine", "sales"]


def test_no_runs_is_not_an_estimate() -> None:
    with pytest.raises(ValueError, match="no runs"):
        across_runs([])


def test_the_table_states_the_rate_and_its_interval() -> None:
    printed = table({"routine": Estimate(11, 12)}, runs=3)

    assert printed.splitlines() == [
        "over 3 runs, 95% interval",
        "routine          11/12  92%  [65%, 99%]",
    ]


@pytest.mark.parametrize("text", ["0", "-2"])
def test_a_number_of_runs_below_one_is_refused(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        run_count(text)


def test_a_number_of_runs_is_read_as_given() -> None:
    assert run_count("3") == 3
