"""Time and identity, injected.

Nothing in the domain calls `datetime.now()` or generates an identifier inline. Both are inputs
to a decision — quiet hours, escalation timeouts, call identity — and code that reaches for
them directly cannot be tested without either waiting or patching a module, and patching a
module is a test that passes because of where a symbol happens to live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class Clock(ABC):
    """The current moment.

    Always timezone-aware. A naive instant means whatever the machine is set to, which is how
    a service that behaves in one region misbehaves in another.
    """

    @abstractmethod
    def now(self) -> datetime:
        """The current instant, with a timezone."""


class IdGenerator(ABC):
    """New identifiers.

    A port so that a test can make them predictable. A test that has to read an identifier out
    of the output in order to assert on it is a test that is describing the implementation.
    """

    @abstractmethod
    def generate(self) -> str:
        """A new, unique value."""
