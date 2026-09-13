"""Span name and attribute checks shared by every tracer, and `NoTracer`, the default."""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from letmehandle.domain.ports.tracing import Span, Tracer

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from letmehandle.domain.ports.tracing import AttributeValue

# Every attribute a span may carry; the call id is an opaque id, never a number.
CALL_ID: Final = "call.id"
SPAN_ATTRIBUTES: Final = frozenset(
    {
        CALL_ID,
        "call.route",
        "call.state",
        "dependency",
        "failure.kind",
        "outcome",
        "platform",
        "stage",
    }
)

# What a call id is replaced by when it could be something other than an opaque id.
UNTRACEABLE_CALL: Final = "untraceable"

_SPAN_NAME: Final = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_TOKEN: Final = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Allowed call id characters; an id needs a letter so it cannot be a phone number.
_CALL_ID: Final = re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9_\-:.]{1,64}")


class SpanAttributeError(ValueError):
    """A span was given a name or an attribute that could carry content, never repeating it."""


def traceable_call_id(value: str) -> str:
    """A call's identifier as observability output may carry it, replaced when not opaque."""
    return value if _CALL_ID.fullmatch(value) else UNTRACEABLE_CALL


def checked_name(name: str) -> str:
    """`name`, once it is known to be a span name, or `SpanAttributeError`."""
    if not _SPAN_NAME.fullmatch(name):
        raise SpanAttributeError(f"{name!r} is not a span name: dotted lower-case words only")
    return name


def checked_attribute(key: str, value: AttributeValue) -> AttributeValue:
    """`value`, once it is known to be structure rather than content, or `SpanAttributeError`."""
    if key not in SPAN_ATTRIBUTES:
        raise SpanAttributeError(
            f"{key!r} is not a span attribute; the attributes are {sorted(SPAN_ATTRIBUTES)}"
        )
    if isinstance(value, bool | int | float):
        return value
    if key == CALL_ID:
        return traceable_call_id(value)
    if not _TOKEN.fullmatch(value):
        raise SpanAttributeError(f"the value of {key!r} is not one a span may carry")
    return value


def checked_attributes(attributes: Mapping[str, AttributeValue]) -> dict[str, AttributeValue]:
    """Every attribute, checked."""
    return {key: checked_attribute(key, value) for key, value in attributes.items()}


class NoTracer(Tracer):
    """Checks every span and sends it nowhere."""

    @contextmanager
    def span(self, name: str, **attributes: AttributeValue) -> Iterator[Span]:
        checked_name(name)
        checked_attributes(attributes)
        yield _NoSpan()


class _NoSpan(Span):
    def set_attribute(self, key: str, value: AttributeValue) -> None:
        checked_attribute(key, value)

    def record_failure(self, error: BaseException) -> None:
        return None
