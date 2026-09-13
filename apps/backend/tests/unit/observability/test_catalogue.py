"""Every metric the product declares is bounded, and none of its labels can carry anything personal.

Asserted over the registry rather than over call sites, so a metric added anywhere is held to it
the moment the module declaring it is imported: this test imports every module first.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from enum import StrEnum
from typing import Final

import pytest

import letmehandle
from letmehandle.observability import catalogue
from letmehandle.observability.catalogue import (
    CODE_NAMED_KEYS,
    LABEL_KEYS,
    LABEL_VALUE,
    NAMED_IN_CODE,
    Instrument,
    MetricLabelError,
    MetricSpec,
    NamedInCode,
    declared,
)
from letmehandle.observability.scrubbing import SENSITIVE_FIELDS


def _every_metric() -> list[MetricSpec]:
    for module in pkgutil.walk_packages(letmehandle.__path__, f"{letmehandle.__name__}."):
        importlib.import_module(module.name)
    return sorted(
        (spec for spec in declared().values() if not spec.name.startswith("tests.")),
        key=lambda spec: spec.name,
    )


EVERY_METRIC: Final = _every_metric()

# Anything a label value could be that identifies somebody or says something: digits in a run, as
# a number or an identifier has, or an at sign.
_IDENTIFYING: Final = re.compile(r"[0-9]{3,}|@")


def test_the_product_declares_its_metrics() -> None:
    names = {spec.name for spec in EVERY_METRIC}

    # A registry that came back empty would pass every test below for the wrong reason.
    assert {"call.transition", "speech.round_trip_seconds", "escalation.delivery"} <= names


@pytest.mark.parametrize("spec", EVERY_METRIC, ids=lambda spec: spec.name)
def test_every_label_is_a_known_dimension_and_none_is_personal(spec: MetricSpec) -> None:
    for key in spec.labels:
        assert key in LABEL_KEYS
        assert re.sub(r"[_\-]", "", key) not in SENSITIVE_FIELDS


@pytest.mark.parametrize("spec", EVERY_METRIC, ids=lambda spec: spec.name)
def test_every_label_is_bounded(spec: MetricSpec) -> None:
    for key, values in spec.labels.items():
        if isinstance(values, NamedInCode):
            # Bounded by how many adapters there are, because only code writes these.
            assert key in CODE_NAMED_KEYS
            continue
        assert 0 < len(values) <= 64, f"{spec.name}.{key} lists {len(values)} values"
        for value in values:
            assert LABEL_VALUE.fullmatch(value)
            assert not _IDENTIFYING.search(value)


class Colour(StrEnum):
    RED = "red"
    GREEN = "green"


def test_a_declaration_returns_the_name_recorded_under_and_takes_values_from_an_enum() -> None:
    name = catalogue.count("tests.catalogue.painted", outcome=Colour)

    assert name == "tests.catalogue.painted"
    assert declared()[name] == MetricSpec(
        name, Instrument.COUNT, {"outcome": frozenset({"red", "green"})}
    )


def test_the_same_declaration_twice_is_one_declaration() -> None:
    first = catalogue.measure("tests.catalogue.twice", provider=NAMED_IN_CODE)

    assert catalogue.measure("tests.catalogue.twice", provider=NAMED_IN_CODE) == first


def test_one_name_declared_two_ways_is_refused() -> None:
    catalogue.count("tests.catalogue.clash", outcome={"a"})

    with pytest.raises(MetricLabelError, match="declared twice"):
        catalogue.count("tests.catalogue.clash", outcome={"a", "b"})


@pytest.mark.parametrize(
    ("name", "labels", "complaint"),
    [
        ("Tests.Catalogue", {}, "not a metric name"),
        ("tests.catalogue.caller", {"caller": {"known"}}, "not a known dimension"),
        ("tests.catalogue.named", {"outcome": NAMED_IN_CODE}, "may be named in code"),
        ("tests.catalogue.sentence", {"outcome": {"the caller hung up"}}, "lower-case token"),
        ("tests.catalogue.number", {"outcome": {"12025550123"}}, "lower-case token"),
        ("tests.catalogue.empty", {"outcome": set()}, "lists no values"),
        ("tests.catalogue.text", {"outcome": "failed"}, "must list its values"),
        ("tests.catalogue.count", {"outcome": 3}, "must list its values"),
    ],
)
def test_a_declaration_that_could_carry_content_is_refused(
    name: str, labels: dict[str, object], complaint: str
) -> None:
    with pytest.raises(MetricLabelError, match=complaint):
        catalogue.count(name, **labels)  # type: ignore[arg-type]  # refused whatever its type

    assert name not in declared()


def test_a_name_written_in_code_says_so_when_printed() -> None:
    assert repr(NAMED_IN_CODE) == "NAMED_IN_CODE"
