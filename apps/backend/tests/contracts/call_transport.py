"""What every call transport must satisfy: do what it declares, and refuse what it does not."""

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
    answering,
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

    async def test_it_can_terminate(self, transport: CallTransport) -> None:
        await transport.terminate(A_CALL)

    async def test_terminating_twice_is_safe(self, transport: CallTransport) -> None:
        # Teardown paths overlap, so a second terminate must not raise.
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
        # A declared capability is implemented, which the type system cannot check.
        for capability, narrow in (
            ("can_answer_under_program_control", answering),
            ("can_screen_before_ringing", screening),
            ("can_bridge_human", bridging),
            ("supports_three_way_call", three_way),
        ):
            if transport.capabilities.has(capability):
                # Narrowing must not raise for a declared capability.
                assert narrow(transport) is not None
            else:
                with pytest.raises(CapabilityNotSupportedError):
                    narrow(transport)

    # -------------------------------------------------- capability behaviours

    async def test_a_call_can_be_answered_and_terminated_where_declared(
        self, transport: CallTransport
    ) -> None:
        if not transport.capabilities.can_answer_under_program_control:
            pytest.skip("this transport's calls are answered by the person holding the handset")
        await answering(transport).answer(A_CALL)
        await transport.terminate(A_CALL)

    def test_a_screening_transport_can_always_let_a_call_ring(
        self, transport: CallTransport
    ) -> None:
        # Letting the call ring is the fallback when no decision can be made safely in time.
        if not transport.capabilities.can_screen_before_ringing:
            pytest.skip("this transport does not see calls before they ring")
        assert ScreeningDecision.ALLOW in screening(transport).screening_decisions()

    def test_a_screening_transport_states_its_deadline(self, transport: CallTransport) -> None:
        if not transport.capabilities.can_screen_before_ringing:
            pytest.skip("this transport does not see calls before they ring")
        assert screening(transport).screening_deadline().total_seconds() > 0

    async def test_only_a_screening_transport_reports_a_screening_decision(
        self, transport: CallTransport
    ) -> None:
        # A transport that cannot screen offers no screening decisions.
        async for event in transport.events():
            if event.screening is not None:
                assert transport.capabilities.can_screen_before_ringing
            break

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
        # A conversational transport's call is both an audio source and an audio sink.
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
        # A transport that can screen offers every screening decision.
        if not transport.capabilities.supports_three_way_call:
            pytest.skip("this transport cannot hold three parties")
        call = three_way(transport)
        for presence in AssistantPresence:
            await call.set_assistant_presence(A_CALL, presence)

    def test_an_undeclared_capability_is_refused_rather_than_attempted(
        self, transport: CallTransport
    ) -> None:
        # An undeclared capability fails the same way on every transport.
        for capability in transport.capabilities.names():
            if transport.capabilities.has(capability):
                continue
            with pytest.raises(CapabilityNotSupportedError) as failure:
                transport.require(capability)
            assert failure.value.capability == capability
            assert failure.value.provider == transport.name
