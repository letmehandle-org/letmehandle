"""In-memory device and escalation-context storage, for the dispatcher without a database.

They keep the promises the repository interfaces make — user scoping, first claim wins, the
first end is kept — so a test that passes here is not passing because the fake is lenient. The
same behaviours are proven against PostgreSQL in the integration suite.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from letmehandle.application.escalation.dispatch import EscalationStores
from letmehandle.domain.ports.repositories import DeviceRepository, EscalationContextRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from letmehandle.domain.models.escalation_context import (
        EscalationContext,
        NotificationDelivery,
    )
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.ports.notification import DeviceToken


@dataclass
class InMemoryDevices(DeviceRepository):
    owners: dict[DeviceToken, UserId] = field(default_factory=dict)

    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        self.owners[token] = user_id

    async def tokens_for(self, user_id: UserId) -> list[DeviceToken]:
        return [token for token, owner in self.owners.items() if owner == user_id]

    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        if self.owners.get(token) == user_id:
            del self.owners[token]


@dataclass
class InMemoryContexts(EscalationContextRepository):
    stored: dict[tuple[UserId, CallId], EscalationContext] = field(default_factory=dict)

    async def claim(self, user_id: UserId, context: EscalationContext) -> bool:
        key = (user_id, context.call_id)
        if key in self.stored:
            return False
        self.stored[key] = context
        return True

    async def get(self, user_id: UserId, call_id: CallId) -> EscalationContext | None:
        return self.stored.get((user_id, call_id))

    async def record_delivery(
        self, user_id: UserId, call_id: CallId, delivery: NotificationDelivery
    ) -> None:
        key = (user_id, call_id)
        if key in self.stored:
            self.stored[key] = replace(self.stored[key], delivery=delivery)

    async def mark_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        key = (user_id, call_id)
        if key not in self.stored:
            return False
        self.stored[key] = self.stored[key].ended(at_instant)
        return True


@dataclass
class InMemoryStores:
    """A store scope over the two, which can be told to fail on a chosen opening."""

    devices: InMemoryDevices = field(default_factory=InMemoryDevices)
    contexts: InMemoryContexts = field(default_factory=InMemoryContexts)
    fail_on_opening: set[int] = field(default_factory=set)
    openings: int = 0

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[EscalationStores]:
        self.openings += 1
        if self.openings in self.fail_on_opening:
            raise ConnectionError("the database is not answering")
        yield EscalationStores(devices=self.devices, contexts=self.contexts)
