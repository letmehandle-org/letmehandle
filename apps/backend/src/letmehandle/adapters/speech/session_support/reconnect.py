"""How long to wait before trying a dropped connection again, and when to stop.

Bounded twice: the number of attempts, so a permanent outage surfaces as a failure somebody can
see rather than a call that hangs in silence; and the delay, so the tenth attempt does not wait
longer than a caller would stay on the line.
"""

from __future__ import annotations

from dataclasses import dataclass

from letmehandle.domain.errors import InvariantError


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """Exponential backoff with jitter.

    Jitter because the failures that drop one connection usually drop all of them. Every session
    retrying on the same schedule arrives at a recovering service in one wave, and knocks it over
    again.
    """

    max_attempts: int = 4
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise InvariantError("a reconnect policy must allow at least one attempt")
        if not 0 < self.initial_delay_seconds <= self.max_delay_seconds:
            raise InvariantError("backoff delays must be positive and the ceiling not below them")

    def delay(self, attempt: int, draw: float) -> float:
        """The wait before attempt `attempt` (from zero), given a uniform `draw` in [0, 1).

        Equal jitter: half the capped exponential delay is fixed and half is random. Never zero,
        so a burst of failures cannot become a tight loop, and never above the ceiling.
        """
        ceiling = min(self.max_delay_seconds, self.initial_delay_seconds * 2.0**attempt)
        return ceiling / 2 + ceiling / 2 * draw
