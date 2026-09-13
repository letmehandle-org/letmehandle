"""How long to wait before trying a dropped connection again, and when to stop."""

from __future__ import annotations

from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """Exponential backoff with jitter, bounded in attempts and in delay."""

    max_attempts: int = 4
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise InvariantError("a reconnect policy must allow at least one attempt")
        if not 0 < self.initial_delay_seconds <= self.max_delay_seconds:
            raise InvariantError("backoff delays must be positive and the ceiling not below them")

    def delay(self, attempt: int, draw: float) -> float:
        """The wait before attempt `attempt`: half the capped delay fixed, half scaled by `draw`."""
        ceiling = min(self.max_delay_seconds, self.initial_delay_seconds * 2.0**attempt)
        return ceiling / 2 + ceiling / 2 * draw


class ReconnectBudget:
    """Attempts spent on replacement connections that have not yet shown they work."""

    def __init__(self) -> None:
        self.spent = 0

    def proven(self) -> None:
        """The latest replacement delivered something real, so the next outage starts afresh."""
        self.spent = 0
