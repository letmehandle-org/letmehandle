"""E: the speech service failing, with the real speech adapter on the call.

Here the assistant's voice is the product's own realtime speech adapter, over a real websocket to
the simulated realtime service, carrying the caller's audio from the telephony stream and speaking
back onto it. The service fails in the three places a call can meet it: refusing the connection as
the call is answered, dropping mid-utterance, and refusing the reconnection after a drop.

What is documented for each: a session that cannot open fails the call and ends it for the caller;
a dropped connection is replaced and the conversation carries on; a replacement refused ends the
call too. In none of them is the caller left on an open line with nobody speaking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.bootstrap import build_speech_provider
from letmehandle.domain.models.call_state import CallState
from tests.e2e.harness import (
    CALLER,
    PATIENCE_SECONDS,
    Emitted,
    Pushes,
    a_user,
    identifying,
    streaming_settings,
    streaming_system,
)
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_model import assess
from tests.support.simulated_realtime_service import SimulatedRealtimeService
from tests.support.simulated_twilio import eventually

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from tests.e2e.app_client import Account
    from tests.e2e.harness import StreamingSystem

pytestmark = pytest.mark.integration

# Narrowband telephone audio: a loud byte is speech to the service, a silent one ends the turn.
SPEECH = b"\x11" * 160
SILENCE = b"\xff" * 160


@pytest.fixture
async def service() -> AsyncIterator[SimulatedRealtimeService]:
    async with SimulatedRealtimeService() as running:
        yield running


def speaking_system(
    database: str, service: SimulatedRealtimeService
) -> AbstractAsyncContextManager[StreamingSystem]:
    settings = streaming_settings(database, speech_endpoint=service.url)
    speech = build_speech_provider(settings, metrics=RecordingMetrics())
    return streaming_system(database, steps=[assess()] * 4, speech=speech, settings=settings)


async def a_turn(system: StreamingSystem, call_id: str) -> None:
    """The caller says something and falls silent, and hears the assistant answer."""
    assistant = await system.provider.assistant_of(call_id)
    before = len(assistant.sent_to_call)
    await system.provider.send_caller_audio(call_id, SPEECH, frames=5)
    await system.provider.send_caller_audio(call_id, SILENCE, frames=5)
    await eventually(lambda: len(assistant.sent_to_call) > before, seconds=PATIENCE_SECONDS)


async def answered(system: StreamingSystem, account: Account, call_id: str) -> None:
    await system.arrives(account, call_id)
    await system.reaches(account, call_id, CallState.AGENT_HANDLING)
    await system.assistant_is_streaming(call_id)


async def test_e_speech_refused_at_connect_ends_the_call_for_the_caller(
    database: str, service: SimulatedRealtimeService, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-speech-refused"
    service.refuse_authentication()
    async with speaking_system(database, service) as system:
        account = await a_user(system)

        await system.arrives(account, call_id)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "failed"
        assert detail["handling"] == "assistant"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.FAILED
        # Hung up rather than held: the caller's conference is over, not waiting in silence.
        assert system.provider.conference_of(call_id).ended
        assert system.dialled() == []
        assert len(service.handshakes) == 1
        assert pushes.sent() == []
        await system.released()
        await service.wait_until_idle()
        assert service.open_connections == 0
        assert emitted.mentions(*identifying(CALLER)) == []


async def test_e_a_connection_dropped_mid_utterance_is_replaced_and_the_call_goes_on(
    database: str, service: SimulatedRealtimeService, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-speech-dropped"
    async with speaking_system(database, service) as system:
        account = await a_user(system)
        await answered(system, account, call_id)
        await a_turn(system, call_id)

        # The caller is part-way through saying something when the connection goes.
        await system.provider.send_caller_audio(call_id, SPEECH, frames=2)
        service.drop_connections()
        await eventually(lambda: len(service.handshakes) == 2, seconds=PATIENCE_SECONDS)
        await a_turn(system, call_id)

        still = await system.stored(account, call_id)
        assert still is not None
        assert still.state is CallState.AGENT_HANDLING
        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "caller_hung_up"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        await system.released()
        await service.wait_until_idle()
        assert service.open_connections == 0
        assert emitted.mentions(*identifying(CALLER)) == []


async def test_e_a_reconnection_refused_ends_the_call_for_the_caller(
    database: str, service: SimulatedRealtimeService, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-speech-lost"
    async with speaking_system(database, service) as system:
        account = await a_user(system)
        await answered(system, account, call_id)
        await a_turn(system, call_id)

        service.refuse_authentication()
        service.drop_connections()
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "failed"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.FAILED
        assert system.provider.conference_of(call_id).ended
        assert len(service.handshakes) >= 2
        await system.released()
        await service.wait_until_idle()
        assert service.open_connections == 0
        assert emitted.mentions(*identifying(CALLER)) == []
