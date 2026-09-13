"""The call transport contract, against the transport that represents a handset."""

from __future__ import annotations

from datetime import timedelta

import pytest

from letmehandle.adapters.transport.android_native.transport import AndroidNativeCallTransport
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    ScreeningDecision,
    screening,
)
from tests.contracts.call_transport import CallTransportContract


class TestAndroidNativeCallTransport(CallTransportContract):
    @pytest.fixture
    async def transport(self) -> AndroidNativeCallTransport:
        native = AndroidNativeCallTransport()
        # One screened call is reported, so the live feed has something to consume.
        await native.publish(
            UserId("user"),
            CallEvent(
                CallEventKind.INCOMING,
                CallId("user:handset-call"),
                EventId("user:handset-event"),
                screening=ScreeningDecision.SILENCE,
            ),
        )
        return native

    def test_it_declares_exactly_what_a_handset_can_do(
        self, transport: AndroidNativeCallTransport
    ) -> None:
        capabilities = transport.capabilities
        assert capabilities.can_screen_before_ringing
        assert capabilities.supports_native_ringing
        # A screening service gets the caller's number, never the audio or control of the call.
        assert not capabilities.can_answer_under_program_control
        assert not capabilities.can_stream_call_audio_to_ai
        assert not capabilities.can_inject_ai_audio
        assert not capabilities.can_bridge_human
        assert not capabilities.supports_three_way_call

    def test_it_can_allow_reject_and_silence_within_the_platform_deadline(
        self, transport: AndroidNativeCallTransport
    ) -> None:
        screener = screening(transport)
        assert screener.screening_decisions() == frozenset(ScreeningDecision)
        assert screener.screening_deadline() == timedelta(seconds=5)
