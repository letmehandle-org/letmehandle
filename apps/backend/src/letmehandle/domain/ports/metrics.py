"""Numbers about how the product is behaving.

A port so that what is measured is decided where the behaviour is, and how it is reported is
decided once, somewhere else. The observability work later replaces the implementation without
touching a single call site.

Labels are for dimensions — which provider, which outcome — and never for content. Nothing a
person said, and no identifier that leads back to them, belongs in a metric: metrics are kept
longer and shared more widely than anything else a service produces.
"""

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
