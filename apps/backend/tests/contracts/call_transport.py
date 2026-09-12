"""What every call transport must satisfy, whatever it is built on.

Subclass this, supply a `transport` fixture, and the suite runs against your implementation.
Both transports in phase 7 subclass it, and so does every simulator used in testing — which is
what makes them comparable rather than merely similar.

The suite is capability-aware on purpose. Two transports that differ in kind cannot be held to
the same behaviour, only to the same honesty: do what you declare, and refuse what you do not.
"""

from __future__ import annotations

from abc import abstractmethod

import pytest

from letmehandle.domain.errors import CapabilityNotSupportedError
from letmehandle.domain.models.audio import AudioFrame
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallTransport,
    ScreeningDecision,
    audio_streaming,
    bridging,
    screening,
    three_way,
)

A_CALL = CallId("contract-call")
A_NUMBER = PhoneNumber.parse("+12025550143")


class CallTransportContract:
    """Every transport must pass this."""

    @pytest.fixture
    @abstractmethod
    def transport(self) -> CallTransport:
        """The implementation under test."""
        raise NotImplementedError

    # ---------------------------------------------------------------- identity

    def test_it_has_a_name(self, transport: CallTransport) -> None:
        # Used in logs, metrics and error messages. Never in a decision.
        assert transport.name.strip()

    def test_it_declares_capabilities(self, transport: CallTransport) -> None:
        assert transport.capabilities.names()

    # ------------------------------------------------------------ the basics

    async def test_it_can_answer_and_terminate(self, transport: CallTransport) -> None:
        await transport.answer(A_CALL)
        await transport.terminate(A_CALL)

    async def test_terminating_twice_is_safe(self, transport: CallTransport) -> None:
        # Teardown runs on paths that overlap — a caller hanging up while the orchestrator is
        # already cleaning up — and a second terminate must not raise.
        await transport.terminate(A_CALL)
        await transport.terminate(A_CALL)

    async def test_events_can_be_consumed(self, transport: CallTransport) -> None:
        async for _event in transport.events():
            break

    # ------------------------------------------------- declarations must hold

    def test_declaring_injection_requires_being_able_to_listen(
        self, transport: CallTransport
    ) -> None:
        # A transport that speaks but cannot hear is an announcement, not a conversation.
        capabilities = transport.capabilities
        if capabilities.can_inject_ai_audio:
            assert capabilities.can_stream_call_audio_to_ai

    def test_declaring_three_way_requires_being_able_to_add_the_third_party(
        self, transport: CallTransport
    ) -> None:
        capabilities = transport.capabilities
        if capabilities.supports_three_way_call:
            assert capabilities.can_bridge_human

    def test_narrowing_matches_what_is_declared(self, transport: CallTransport) -> None:
        # The check the type system cannot make: that a declaration and an implementation
        # agree. A transport claiming a capability it has not implemented fails here rather
        # than with an attribute error, mid-call, in front of somebody.
        for capability, narrow in (
            ("can_screen_before_ringing", screening),
            ("can_bridge_human", bridging),
            ("supports_three_way_call", three_way),
        ):
            if transport.capabilities.has(capability):
                # Narrowing returns the transport itself; what matters is that it does not
                # raise, which is the disagreement this catches.
                assert narrow(transport) is not None
            else:
                with pytest.raises(CapabilityNotSupportedError):
                    narrow(transport)

    # -------------------------------------------------- capability behaviours

    async def test_screening_works_where_it_is_declared(self, transport: CallTransport) -> None:
        if not transport.capabilities.can_screen_before_ringing:
            pytest.skip("this transport does not see calls before they ring")
        for decision in ScreeningDecision:
            await screening(transport).screen(A_CALL, decision)

    async def test_audio_flows_both_ways_where_it_is_declared(
        self, transport: CallTransport
    ) -> None:
        if not transport.capabilities.supports_agent_conversation:
            pytest.skip("this transport cannot carry the call's audio")
        streaming = audio_streaming(transport)
        frame = AudioFrame(b"\x00\x01", streaming.audio_format())
        await streaming.inject_audio(A_CALL, frame)
        async for _incoming in streaming.stream_audio(A_CALL):
            break

    async def test_a_call_is_a_conversations_source_and_sink_where_declared(
        self, transport: CallTransport
    ) -> None:
        # A conversation runs over a source and a sink. A call has to be both, or the speech
        # layer grows a second way of talking that only calls use.
        if not transport.capabilities.supports_agent_conversation:
            pytest.skip("this transport cannot carry the call's audio")
        streaming = audio_streaming(transport)
        source = streaming.audio_source(A_CALL)
        sink = streaming.audio_sink(A_CALL)
        assert source.format == streaming.audio_format()
        await sink.write(AudioFrame(b"\x00\x01", sink.format))
        await sink.discard()
        async for _incoming in source.frames():
            break

    async def test_a_third_party_can_be_added_and_removed_where_declared(
        self, transport: CallTransport
    ) -> None:
        if not transport.capabilities.can_bridge_human:
            pytest.skip("this transport cannot add anyone to a call")
        bridge = bridging(transport)
        await bridge.add_participant(A_CALL, A_NUMBER)
        await bridge.remove_participant(A_CALL, A_NUMBER)

    async def test_every_assistant_presence_can_be_chosen_where_three_way_is_declared(
        self, transport: CallTransport
    ) -> None:
        # The policy chooses among these; a transport that offered three of the four would make
        # a preference that silently does nothing.
        if not transport.capabilities.supports_three_way_call:
            pytest.skip("this transport cannot hold three parties")
        call = three_way(transport)
        for presence in AssistantPresence:
            await call.set_assistant_presence(A_CALL, presence)

    def test_an_undeclared_capability_is_refused_rather_than_attempted(
        self, transport: CallTransport
    ) -> None:
        # The property that lets the orchestrator stay one piece of code: asking for something
        # a transport cannot do fails the same way for every transport.
        for capability in transport.capabilities.names():
            if transport.capabilities.has(capability):
                continue
            with pytest.raises(CapabilityNotSupportedError) as failure:
                transport.require(capability)
            assert failure.value.capability == capability
            assert failure.value.provider == transport.name
