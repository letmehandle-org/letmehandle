"""What a span may be called and carry, checked for every tracer, and the tracer that sends nowhere.

The default is `NoTracer`. A deployment with no tracing backend runs with it, and it checks what it
is given exactly as the exporting tracer does, so a span attribute that would carry content fails
the test that first reaches it whichever tracer that test runs with.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING, Final

from letmehandle.domain.ports.tracing import Span, Tracer

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from letmehandle.domain.ports.tracing import AttributeValue

# Every attribute a span may carry. The call's identifier is how one call's spans are found
# together, and it is the provider's or the handset's opaque id, never a number.
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
# What providers' and handsets' call ids are made of. A handset chooses its own, so one is only
# carried when it has a letter in it too: digits and separators alone could be a phone number.
_CALL_ID: Final = re.compile(r"(?=.*[A-Za-z])[A-Za-z0-9_\-:.]{1,64}")


class SpanAttributeError(ValueError):
    """A span was given a name or an attribute that could carry content.

    The value is never repeated in the message, for the reason `MetricLabelError` gives.
    """


def traceable_call_id(value: str) -> str:
    """A call's identifier as observability output may carry it.

    Replaced rather than refused when it could be something other than an opaque id: the id arrives
    from outside, and a call is not failed for the shape of the identifier somebody else gave it.
    """
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
