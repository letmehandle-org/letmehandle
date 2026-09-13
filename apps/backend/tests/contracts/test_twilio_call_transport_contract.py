"""The call transport contract, against the streaming transport and a simulated provider."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.ports.call_transport import answering
from tests.contracts.call_transport import A_CALL, CallTransportContract
from tests.support.simulated_twilio import simulated_deployment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport


class TestTwilioCallTransport(CallTransportContract):
    # Which line by region the account is, or None for the single line at the root.
    line_name: str | None = None

    @pytest.fixture
    async def transport(self) -> AsyncIterator[TwilioCallTransport]:
        async with simulated_deployment(
            collect_events=False, line_name=self.line_name
        ) as deployment:
            await deployment.provider.place_call(A_CALL.value)
            await answering(deployment.transport).answer(A_CALL)
            await deployment.settle()
            await deployment.provider.send_caller_audio(A_CALL.value, b"\xff" * 160)
            yield deployment.transport
            await deployment.settle()

    def test_it_declares_the_streaming_capabilities_and_nothing_else(
        self, transport: TwilioCallTransport
    ) -> None:
        capabilities = transport.capabilities
        assert capabilities.can_answer_under_program_control
        assert capabilities.supports_agent_conversation
        assert capabilities.can_bridge_human
        assert capabilities.supports_three_way_call
        # It never sees a call before the call connects, and it rings nothing natively.
        assert not capabilities.can_screen_before_ringing
        assert not capabilities.supports_native_ringing


class TestTwilioLineByRegionCallTransport(TestTwilioCallTransport):
    """The same transport as one line of several, called back under its own prefix (D-041)."""

    line_name = "in"
