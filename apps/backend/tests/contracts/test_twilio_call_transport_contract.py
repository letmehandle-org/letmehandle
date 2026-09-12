"""The call transport contract, against the streaming transport and a simulated provider.

The same suite the in-memory transports pass. The call the suite names is placed with the
simulated provider first — answered into its conference, with the assistant's leg streaming and
a frame of the caller's audio already on its way — because this transport, unlike a fake, only
knows about calls that have actually arrived.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.contracts.call_transport import A_CALL, CallTransportContract
from tests.support.simulated_twilio import simulated_deployment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport


class TestTwilioCallTransport(CallTransportContract):
    @pytest.fixture
    async def transport(self) -> AsyncIterator[TwilioCallTransport]:
        async with simulated_deployment(collect_events=False) as deployment:
            await deployment.provider.place_call(A_CALL.value)
            await deployment.transport.answer(A_CALL)
            await deployment.settle()
            await deployment.provider.send_caller_audio(A_CALL.value, b"\xff" * 160)
            yield deployment.transport
            await deployment.settle()

    def test_it_declares_the_streaming_capabilities_and_nothing_else(
        self, transport: TwilioCallTransport
    ) -> None:
        capabilities = transport.capabilities
        assert capabilities.supports_agent_conversation
        assert capabilities.can_bridge_human
        assert capabilities.supports_three_way_call
        # It never sees a call before the call connects, and it rings nothing natively.
        assert not capabilities.can_screen_before_ringing
        assert not capabilities.supports_native_ringing
