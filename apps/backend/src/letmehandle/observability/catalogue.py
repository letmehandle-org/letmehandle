"""Every metric the product records, declared beside its call site with bounded label values."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

# Every dimension a metric may be broken down by.
LABEL_KEYS: Final = frozenset({"kind", "outcome", "platform", "provider", "retryable", "stage"})

# The dimensions whose values may be names written in code rather than a listed set.
CODE_NAMED_KEYS: Final = frozenset({"platform", "provider"})

MAX_LABEL_VALUE_LENGTH: Final = 32

# A letter first, so that neither a number nor anything shaped like one is a dimension.
LABEL_VALUE: Final = re.compile(rf"[a-z][a-z0-9_]{{0,{MAX_LABEL_VALUE_LENGTH - 1}}}")
_METRIC_NAME: Final = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")


class MetricLabelError(ValueError):
    """A metric was declared or recorded with a name or a label that could carry content."""


class Instrument(StrEnum):
    """Whether a metric counts occurrences or measures something that varies."""

    COUNT = "count"
    MEASURE = "measure"


class NamedInCode:
    """A label whose values are names an adapter writes in code: a provider's, a platform's."""

    def __repr__(self) -> str:
        return "NAMED_IN_CODE"


NAMED_IN_CODE: Final = NamedInCode()

type LabelValues = frozenset[str] | NamedInCode


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One metric: what it records, and every value each of its labels may take."""

    name: str
    instrument: Instrument
    labels: Mapping[str, LabelValues]


_DECLARED: dict[str, MetricSpec] = {}


def count(name: str, **labels: Iterable[str] | NamedInCode) -> str:
    """Declare a counted metric and return its name, for the call site to record under."""
    return _declare(name, Instrument.COUNT, labels)


def measure(name: str, **labels: Iterable[str] | NamedInCode) -> str:
    """Declare a measured metric and return its name, for the call site to record under."""
    return _declare(name, Instrument.MEASURE, labels)


def declared() -> Mapping[str, MetricSpec]:
    """Every metric declared by the modules imported so far."""
    return dict(_DECLARED)


def spec_for(name: str, instrument: Instrument) -> MetricSpec:
    """The declaration of `name` as `instrument`, or `MetricLabelError`."""
    spec = _DECLARED.get(name)
    if spec is None:
        raise MetricLabelError(
            f"{name!r} is not a declared metric; declare it where it is recorded"
        )
    if spec.instrument is not instrument:
        raise MetricLabelError(f"{name!r} is declared as a {spec.instrument}, not a {instrument}")
    return spec


def _declare(name: str, instrument: Instrument, labels: Mapping[str, object]) -> str:
    if not _METRIC_NAME.fullmatch(name):
        raise MetricLabelError(f"{name!r} is not a metric name: dotted lower-case words only")
    spec = MetricSpec(
        name, instrument, {key: _values(name, key, given) for key, given in labels.items()}
    )
    existing = _DECLARED.setdefault(name, spec)
    if existing != spec:
        raise MetricLabelError(f"{name!r} is declared twice, differently")
    return name


def _values(name: str, key: str, given: object) -> LabelValues:
    if key not in LABEL_KEYS:
        raise MetricLabelError(
            f"{name}: {key!r} is not a known dimension; the dimensions are {sorted(LABEL_KEYS)}"
        )
    if isinstance(given, NamedInCode):
        if key not in CODE_NAMED_KEYS:
            raise MetricLabelError(f"{name}: only {sorted(CODE_NAMED_KEYS)} may be named in code")
        return given
    if not isinstance(given, Iterable) or isinstance(given, str):
        raise MetricLabelError(f"{name}: {key!r} must list its values")
    values: set[str] = set()
    for each in given:
        value = each.value if isinstance(each, Enum) else each
        if not isinstance(value, str) or not LABEL_VALUE.fullmatch(value):
            raise MetricLabelError(
                f"{name}: every value of {key!r} is a lower-case token of at most "
                f"{MAX_LABEL_VALUE_LENGTH} characters, starting with a letter"
            )
        values.add(value)
    if not values:
        raise MetricLabelError(f"{name}: {key!r} lists no values")
    return frozenset(values)
