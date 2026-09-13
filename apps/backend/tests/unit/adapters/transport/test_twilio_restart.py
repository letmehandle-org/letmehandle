"""A restart over the streaming transport, with calls left behind still up at the provider."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from letmehandle.adapters.transport.twilio.rest import CallRecord
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
OUR_NUMBER = PhoneNumber.parse("+12025550100")
USERS_LINE = PhoneNumber.parse("+12025550143")
STARTED = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)


def a_new_process(api: RecordingApi) -> TwilioCallTransport:
    return TwilioCallTransport(
        config=TwilioConfig(
            account_id="account-for-tests",
            app_id="app-for-tests",
            numbers=(OUR_NUMBER,),
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
            transports=(transport,),
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


async def test_a_restart_ends_the_users_phone_still_ringing_into_the_old_conference() -> None:
    # A leg still ringing is not in the conference, so ending the conference leaves it ringing.
    api = RecordingApi()
    api.found = CallRecord(to=OUR_NUMBER.value, forwarded_from="+1 (202) 555-0143")
    transport = a_new_process(api)
    try:
        await transport.terminate(LEFT_RUNNING)
    finally:
        await transport.close()

    assert api.looked_up == [LEFT_RUNNING.value]
    # From the number the call reached, to the line that forwarded it: the user's number.
    assert api.ended_between == [(OUR_NUMBER.value, USERS_LINE.value)]


async def test_a_call_that_reached_a_number_not_configured_is_ended_from_the_first() -> None:
    api = RecordingApi()
    api.found = CallRecord(to="+12025550199", forwarded_from=USERS_LINE.value)
    transport = a_new_process(api)
    try:
        await transport.terminate(LEFT_RUNNING)
    finally:
        await transport.close()

    assert api.ended_between == [(OUR_NUMBER.value, USERS_LINE.value)]


@pytest.mark.parametrize(
    "found",
    [None, CallRecord(to=OUR_NUMBER.value, forwarded_from=None)],
    ids=["unknown to the provider", "never forwarded"],
)
async def test_a_call_that_could_have_dialled_nobody_ends_no_other_leg(
    found: CallRecord | None,
) -> None:
    # Not forwarded is nobody's call, and nobody's call never dials a user.
    api = RecordingApi()
    api.found = found
    transport = a_new_process(api)
    try:
        await transport.terminate(LEFT_RUNNING)
    finally:
        await transport.close()

    assert api.looked_up == [LEFT_RUNNING.value]
    assert api.ended_between == []
