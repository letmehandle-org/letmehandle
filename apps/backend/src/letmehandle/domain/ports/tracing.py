"""Timed spans following one call, with attributes carrying structure, never content (D-038)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

type AttributeValue = str | int | float | bool


class Span(ABC):
    """One timed piece of work, inside whichever span was current when it began."""

    @abstractmethod
    def set_attribute(self, key: str, value: AttributeValue) -> None:
        """Describe the work with one more attribute, learned while it ran."""

    @abstractmethod
    def record_failure(self, error: BaseException) -> None:
        """Mark the work as failed, by the kind of failure and never by its message."""


class Tracer(ABC):
    """Begins spans."""

    @abstractmethod
    def span(self, name: str, **attributes: AttributeValue) -> AbstractContextManager[Span]:
        """A span for the `with` block, failed by an exception leaving it, which is re-raised."""
