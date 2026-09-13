"""Following one call through everything it touches, as a tree of timed spans.

A port, so that where spans begin and end is decided where the work is, and where they go is
decided once, in bootstrap: a tracing backend when a deployment has one, and nowhere when it does
not. Nothing that opens a span can tell which.

Attributes are for structure and never for content, for the reason metrics labels are: a span is
exported to a system kept longer and read more widely than the call it describes. So they are keyed
from a short, fixed list, and a value is a token, a number or a call's own identifier — never what
anybody said and never a number anybody dialled.
"""

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
        """A span for the work inside the `with` block, ended when the block is left.

        An exception leaving the block marks the span failed and is raised on unchanged.
        """
