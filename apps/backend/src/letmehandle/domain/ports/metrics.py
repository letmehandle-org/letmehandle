"""Numbers about how the product is behaving, labelled by dimension and never by content (D-038)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class MetricsRecorder(ABC):
    """Records observations and counts."""

    @abstractmethod
    def observe(self, name: str, value: float, labels: Mapping[str, str] | None = None) -> None:
        """One measurement of something that varies, such as a latency in seconds."""

    @abstractmethod
    def increment(self, name: str, labels: Mapping[str, str] | None = None) -> None:
        """One occurrence of something worth counting, such as a reconnection."""
