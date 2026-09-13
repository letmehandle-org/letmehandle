"""A restart over the streaming transport, where the calls left behind are still up at the provider.

Each test here reproduced a defect before its fix, and keeps it fixed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from letmehandle.adapters.transport.twilio.signature import SignatureVerifier
from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport, TwilioConfig
from letmehandle.application.escalation.dispatch import EscalationDispatcher
from letmehandle.application.orchestration.recovery import Recovery
from letmehandle.application.resilience.circuit import Circuits
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import CallHandling, CallSession, Participant, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.observability.tracing import NoTracer
from tests.contracts.fakes import FixedClock
from tests.support.escalation_stores import InMemoryStores
from tests.support.orchestration import OWNER, QUICK, MemoryCallStores
from tests.support.recording_metrics import RecordingMetrics
from tests.unit.adapters.transport.test_twilio_transport import RecordingApi

LEFT_RUNNING = CallId("CAsim-left-running")
STARTED = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)


def a_new_process(api: RecordingApi) -> TwilioCallTransport:
    return TwilioCallTransport(
        config=TwilioConfig(
            account_id="account-for-tests",
            app_id="app-for-tests",
            numbers=(PhoneNumber.parse("+12025550100"),),
        ),
        api=api,
        verifier=SignatureVerifier(
            auth_token="token-for-tests", public_base_url="https://calls.example.com"
        ),
    )


async def test_a_restart_ends_the_call_left_running_at_the_provider() -> None:
    api = RecordingApi()
    # A new process: the transport holds nothing about calls the last one carried.
    transport = a_new_process(api)
    stores = MemoryCallStores()
    await stores.with_owner()
    await stores.calls.save(
        CallSession.restore(
            id=LEFT_RUNNING,
            user_id=OWNER,
            caller=Caller(),
            started_at=STARTED,
            state=CallState.AGENT_HANDLING,
            participants=(Participant(ParticipantRole.AGENT, STARTED),),
            ended_at=None,
            handling=CallHandling.ASSISTANT,
        )
    )
    metrics = RecordingMetrics()
    try:
        ended = await Recovery(
            transport=transport,
            stores=stores.scope,
            dispatcher=EscalationDispatcher(
                providers=[],
                stores=InMemoryStores().scope,
                metrics=metrics,
                tracer=NoTracer(),
                circuits=Circuits(metrics=metrics),
            ),
            clock=FixedClock(),
            metrics=metrics,
            bounds=QUICK,
        ).end_unfinished()
    finally:
        await transport.close()

    assert ended == 1
    assert stores.call(LEFT_RUNNING.value).state is CallState.FAILED
    # The caller is still in the conference the stopped process put them in, hearing nothing.
    assert (LEFT_RUNNING.value, "completed") in api.ended_calls
    # And whoever the stopped process dialled into it with them, found by the conference's name.
    assert api.ended_conference_names == [f"call-{LEFT_RUNNING.value}"]


async def test_a_call_left_running_is_still_tried_in_full_when_the_provider_refuses_a_part() -> (
    None
):
    api = RecordingApi()
    transport = a_new_process(api)
    api.failure = ProviderError("twilio", "refused", retryable=True)
    try:
        with pytest.raises(ProviderError, match="refused"):
            await transport.terminate(LEFT_RUNNING)
        # Asking again is safe: what had already ended is done, not a failure.
        await transport.terminate(LEFT_RUNNING)
    finally:
        await transport.close()

    assert api.ended_conference_names == [f"call-{LEFT_RUNNING.value}"] * 2
    assert api.ended_calls == [(LEFT_RUNNING.value, "completed")]
