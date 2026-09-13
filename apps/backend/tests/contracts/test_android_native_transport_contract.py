"""The call transport contract, against the transport that represents a handset.

The same suite the streaming transport runs. What differs is declared rather than special-cased:
this transport screens before ringing and uses the handset's own ringing, and everything it does
not declare — answering, audio, adding a person — the suite proves it refuses.
"""

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
        # A handset has reported one screened call, so there is something to consume: the feed
        # is live, and an empty one waits for the next report rather than finishing.
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
        # The platform gives a screening service the caller's number, never the call's audio,
        # and no way to take or extend a call it does not own.
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
