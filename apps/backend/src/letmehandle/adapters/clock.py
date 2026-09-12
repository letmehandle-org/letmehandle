"""The real clock and the real identifier generator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from letmehandle.domain.ports.clock import Clock, IdGenerator


class SystemClock(Clock):
    """The machine's clock, always in UTC.

    UTC rather than local time, everywhere, with conversion to a user's zone happening only
    where something is shown to them. A service that stores local time cannot say what a
    timestamp meant once it has moved between regions.
    """

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class UUIDGenerator(IdGenerator):
    """Version 4 identifiers.

    Random rather than sequential: an identifier that can be guessed from a previous one tells
    an attacker how many accounts exist and lets them ask about the next.
    """

    def generate(self) -> str:
        return str(uuid.uuid4())
