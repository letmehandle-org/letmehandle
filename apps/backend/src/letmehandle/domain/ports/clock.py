"""Time and identifiers, injected so that nothing in the domain reads either directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class Clock(ABC):
    """The current moment."""

    @abstractmethod
    def now(self) -> datetime:
        """The current instant, always timezone-aware."""


class IdGenerator(ABC):
    """New identifiers."""

    @abstractmethod
    def generate(self) -> str:
        """A new, unique value."""
