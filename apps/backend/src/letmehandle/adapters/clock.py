"""The real clock and the real identifier generator."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from letmehandle.domain.ports.clock import Clock, IdGenerator


class SystemClock(Clock):
    """The machine's clock, always in UTC."""

    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class UUIDGenerator(IdGenerator):
    """Random version 4 identifiers, so none can be guessed from another."""

    def generate(self) -> str:
        return str(uuid.uuid4())
