"""A handset's own calls, as a transport.

The handset does the work. Android's call screening service is given a new incoming call before
it rings and must answer within five seconds, so the decision is taken there, from a snapshot of
the user's deterministic call rules the app keeps on the handset. What the backend holds is this:
the handset, represented as a transport whose events arrive over an authenticated API after the
fact.

What that means for each part of the port, stated because each is a choice:

  Capabilities. It screens before ringing and uses the platform's own ringing. It cannot answer
  a call, carry a call's audio, or add anybody, because Android gives an application none of
  those for a SIM call without being the phone app — and even then never the audio.

  Screening. Offered as what the handset can decide and how long it has, not as a command. A
  decision sent from here would arrive after the phone had rung.

  Terminate. Nobody on a server can hang up a handset's call. This releases the transport's
  interest in the call: anything the handset reports about it afterwards is still stored by
  the reporting service, and is no longer handed to whoever is consuming these events.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
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

    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.ports.call_transport import CallEvent

# CallScreeningService.onScreenCall: "A CallScreeningService must respond to a call within 5
# seconds. After this time, the framework will unbind from the CallScreeningService and ignore
# its response."
PLATFORM_SCREENING_DEADLINE: Final = timedelta(seconds=5)

# How many events may wait for a consumer. Every one of them is already stored by the time it
# reaches here, so a full feed loses nothing durable; it is bounded so that a deployment with no
# consumer yet cannot grow without limit.
DEFAULT_FEED_LIMIT: Final = 1_000

# How many released calls are remembered. A late report for a call older than this is handed on
# rather than suppressed, which is the safe direction to be wrong in.
DEFAULT_RELEASED_LIMIT: Final = 10_000


class AndroidNativeCallTransport(CallTransport, CallEventSink):
    """The handset's calls, fed by what the handset reports."""

    def __init__(
        self,
        *,
        feed_limit: int = DEFAULT_FEED_LIMIT,
        released_limit: int = DEFAULT_RELEASED_LIMIT,
    ) -> None:
        self._feed: asyncio.Queue[CallEvent] = asyncio.Queue(maxsize=feed_limit)
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
            yield await self._feed.get()

    async def terminate(self, call_id: CallId) -> None:
        self._released[call_id] = None
        self._released.move_to_end(call_id)
        while len(self._released) > self._released_limit:
            self._released.popitem(last=False)

    async def publish(self, event: CallEvent) -> None:
        if event.call_id in self._released:
            self._logger.info("call_event_after_release", kind=event.kind.value)
            return
        try:
            self._feed.put_nowait(event)
        except asyncio.QueueFull:
            # Not swallowed: the report is stored, and this says that the live feed fell behind.
            self._logger.warning(
                "call_event_feed_full",
                kind=event.kind.value,
                waiting=self._feed.qsize(),
            )

    def screening_decisions(self) -> frozenset[ScreeningDecision]:
        # CallResponse.Builder: setDisallowCall with setRejectCall rejects, setSilenceCall rings
        # without sound, and a response with neither lets the call ring.
        return frozenset(ScreeningDecision)

    def screening_deadline(self) -> timedelta:
        return PLATFORM_SCREENING_DEADLINE
