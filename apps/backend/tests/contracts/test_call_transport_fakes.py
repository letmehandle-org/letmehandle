"""The contract, run against both shapes of transport, and against one that lies."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import CapabilityNotSupportedError
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    answering,
    audio_streaming,
    bridging,
    screening,
)
from tests.contracts.call_transport import CallTransportContract
from tests.contracts.fakes import LyingTransport, ScreeningOnlyTransport, StreamingTransport


class TestScreeningOnlyTransport(CallTransportContract):
    """A transport shaped like a platform's own call screening."""

    @pytest.fixture
    def transport(self) -> ScreeningOnlyTransport:
        return ScreeningOnlyTransport()

    def test_it_claims_no_audio_it_cannot_supply(self, transport: ScreeningOnlyTransport) -> None:
        # The honesty the whole architecture rests on. A platform that screens calls does not
        # hand an application the audio of one, and claiming otherwise would produce an
        # assistant that answers into silence.
        assert not transport.capabilities.can_stream_call_audio_to_ai
        assert not transport.capabilities.supports_agent_conversation
        assert not transport.capabilities.can_bridge_human

    def test_it_cannot_answer_a_call_the_handset_owns(
        self, transport: ScreeningOnlyTransport
    ) -> None:
        assert not transport.capabilities.can_answer_under_program_control


class TestStreamingTransport(CallTransportContract):
    """A transport shaped like programmable telephony."""

    @pytest.fixture
    def transport(self) -> StreamingTransport:
        return StreamingTransport()

    def test_it_supports_the_conversation_and_the_bridge(
        self, transport: StreamingTransport
    ) -> None:
        assert transport.capabilities.supports_agent_conversation
        assert transport.capabilities.can_bridge_human
        assert transport.capabilities.supports_three_way_call

    def test_it_never_sees_a_call_before_it_connects(self, transport: StreamingTransport) -> None:
        assert not transport.capabilities.can_screen_before_ringing


class TestDeclarationsAreChecked:
    """The one failure the type system cannot catch."""

    def test_a_transport_that_claims_bridging_without_implementing_it_is_caught(self) -> None:
        with pytest.raises(CapabilityNotSupportedError) as failure:
            bridging(LyingTransport())
        assert failure.value.capability == "can_bridge_human"

    def test_the_same_holds_for_answering(self) -> None:
        # The lying transport declares answering and has no method for it.
        with pytest.raises(CapabilityNotSupportedError) as failure:
            answering(LyingTransport())
        assert failure.value.capability == "can_answer_under_program_control"

    def test_the_same_holds_for_screening(self) -> None:
        with pytest.raises(CapabilityNotSupportedError):
            screening(LyingTransport())

    def test_and_for_audio_streaming(self) -> None:
        # A transport declaring it can carry audio, with no methods to do it. Without this
        # check the failure arrives as an attribute error, mid-call, in front of a caller.
        with pytest.raises(CapabilityNotSupportedError) as failure:
            audio_streaming(LyingTransport())
        assert failure.value.capability == "can_stream_call_audio_to_ai"

    async def test_a_truthful_transport_narrows_to_a_usable_object(self) -> None:
        transport = StreamingTransport()
        await bridging(transport).add_participant(CallId("c"), PhoneNumber.parse("+12025550143"))
        assert transport.participants == [PhoneNumber.parse("+12025550143")]
