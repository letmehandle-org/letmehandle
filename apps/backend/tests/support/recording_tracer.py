"""A tracer that keeps every span it is given, with its parent, so a test can read the tree back.

It checks names and attributes exactly as the real tracers do, so a span that would carry content
fails the test that opened it here too.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.ports.tracing import Span, Tracer
from letmehandle.observability.tracing import checked_attribute, checked_attributes, checked_name

if TYPE_CHECKING:
    from collections.abc import Iterator

    from letmehandle.domain.ports.tracing import AttributeValue


@dataclass
class RecordedSpan:
    """One span: what it was called, what it carried, whose child it was, and how it ended."""

    name: str
    attributes: dict[str, AttributeValue]
    parent: RecordedSpan | None
    failure: FailureKind | None = None
    ended: bool = False
    children: list[RecordedSpan] = field(default_factory=list)

    def ancestors(self) -> list[str]:
        """The names of every span above this one, nearest first."""
        names: list[str] = []
        current = self.parent
        while current is not None:
            names.append(current.name)
            current = current.parent
        return names


class RecordingTracer(Tracer):
    """Every span, in the order each began."""

    def __init__(self) -> None:
        self.spans: list[RecordedSpan] = []
        self._current: ContextVar[RecordedSpan | None] = ContextVar("recorded_span", default=None)

    @contextmanager
    def span(self, name: str, **attributes: AttributeValue) -> Iterator[Span]:
        parent = self._current.get()
        recorded = RecordedSpan(checked_name(name), checked_attributes(attributes), parent)
        if parent is not None:
            parent.children.append(recorded)
        self.spans.append(recorded)
        token = self._current.set(recorded)
        try:
            yield _RecordingSpan(recorded)
        except asyncio.CancelledError:
            recorded.attributes["outcome"] = "cancelled"
            raise
        except Exception as error:
            recorded.failure = classify(error).kind
            raise
        finally:
            self._current.reset(token)
            recorded.ended = True

    def named(self, name: str) -> list[RecordedSpan]:
        return [span for span in self.spans if span.name == name]


class _RecordingSpan(Span):
    def __init__(self, recorded: RecordedSpan) -> None:
        self._recorded = recorded

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self._recorded.attributes[key] = checked_attribute(key, value)

    def record_failure(self, error: BaseException) -> None:
        self._recorded.failure = classify(error).kind
