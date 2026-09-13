"""A handset's calls as a transport: screened on the handset, reported after the fact (D-028)."""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from letmehandle.domain.ports.call_transport import (
    CallTransport,
    ScreeningDecision,
    TransportCapabilities,
)
from letmehandle.domain.ports.reported_calls import CallEventSink
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.ports.call_transport import CallEvent

# How long Android's call screening service has to respond to a call.
PLATFORM_SCREENING_DEADLINE: Final = timedelta(seconds=5)

# How many of one user's already stored events may wait for a consumer.
DEFAULT_PER_USER_FEED_LIMIT: Final = 100

# How many released calls are remembered; a report for an older one is handed on.
DEFAULT_RELEASED_LIMIT: Final = 10_000


class AndroidNativeCallTransport(CallTransport, CallEventSink):
    """The handset's calls, fed by what the handset reports."""

    def __init__(
        self,
        *,
        per_user_feed_limit: int = DEFAULT_PER_USER_FEED_LIMIT,
        released_limit: int = DEFAULT_RELEASED_LIMIT,
    ) -> None:
        self._feed: asyncio.Queue[tuple[UserId, CallEvent]] = asyncio.Queue()
        self._waiting: Counter[UserId] = Counter()
        self._per_user_feed_limit = per_user_feed_limit
        self._released: OrderedDict[CallId, None] = OrderedDict()
        self._released_limit = released_limit
        self._logger = get_logger(__name__)

    @property
    def name(self) -> str:
        return "android-native"

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(can_screen_before_ringing=True, supports_native_ringing=True)

    async def events(self) -> AsyncIterator[CallEvent]:
        while True:
            user_id, event = await self._feed.get()
            self._waiting[user_id] -= 1
            if not self._waiting[user_id]:
                del self._waiting[user_id]
            yield event

    async def terminate(self, call_id: CallId) -> None:
        self._released[call_id] = None
        self._released.move_to_end(call_id)
        while len(self._released) > self._released_limit:
            self._released.popitem(last=False)

    async def publish(self, user_id: UserId, event: CallEvent) -> None:
        if event.call_id in self._released:
            self._logger.info("call_event_after_release", kind=event.kind.value)
            return
        if self._waiting[user_id] >= self._per_user_feed_limit:
            # The report is stored; only the live feed fell behind this user's handset.
            self._logger.warning(
                "call_event_feed_full",
                kind=event.kind.value,
                waiting=self._waiting[user_id],
            )
            return
        self._waiting[user_id] += 1
        self._feed.put_nowait((user_id, event))

    def screening_decisions(self) -> frozenset[ScreeningDecision]:
        # A handset can reject, silence, or let a call ring.
        return frozenset(ScreeningDecision)

    def screening_deadline(self) -> timedelta:
        return PLATFORM_SCREENING_DEADLINE
