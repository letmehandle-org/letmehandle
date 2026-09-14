"""One escalated call read back through logs, spans, metrics and diagnostics, nothing personal."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.models.call_state import CallState
from tests.e2e.harness import (
    DIAGNOSTICS_TOKEN,
    DRIVER,
    USERS_LINE,
    Emitted,
    Pushes,
    a_user,
    identifying,
    streaming_system,
)
from tests.support.recording_tracer import RecordingTracer
from tests.support.scripted_model import assess
from tests.support.simulated_twilio import Answering

if TYPE_CHECKING:
    from tests.e2e.app_client import Json
    from tests.e2e.harness import StreamingSystem

pytestmark = pytest.mark.integration

ESTABLISHED = "A courier has a parcel that needs a signature."
DELIVERY = assess(
    intent="delivery_in_progress",
    importance="notable",
    needs_the_users_decision=True,
    caller_summary=ESTABLISHED,
)
DRIVER_SAYS = "Hello, I have a parcel for flat nine and it needs a signature."
DEVICE_TOKENS = ("ios-device-token-e2e", "android-device-token-e2e")


async def diagnostics(system: StreamingSystem, path: str) -> Json:
    response = await system.api.http.get(
        path, headers={"Authorization": f"Bearer {DIAGNOSTICS_TOKEN}"}
    )
    assert response.status_code == 200, response.text
    body: Json = response.json()
    return body


async def test_a_call_is_traced_timed_and_diagnosed_by_its_id_and_nothing_personal_leaves(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-observed"
    async with streaming_system(database, steps=[DELIVERY]) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.ANSWERS

        await system.arrives(account, call_id, caller=DRIVER)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.assistant_is_streaming(call_id)
        await system.caller_says(DRIVER_SAYS)
        await system.joined_by_the_user(account, call_id)
        live = await diagnostics(system, "/diagnostics/calls")

        await system.provider.caller_hangs_up(call_id)
        await system.ended(account, call_id)
        await system.released()
        timeline = await diagnostics(system, f"/diagnostics/calls/{call_id}")
        measurements = await diagnostics(system, "/diagnostics/metrics")
        readiness = (await system.api.http.get("/health/ready")).json()
        tracer = system.app.state.observability.tracer
        assert isinstance(tracer, RecordingTracer)

    # Live, the call stood where it was: the user on it with the caller.
    [standing] = live["calls"]
    assert (standing["call_id"], standing["state"]) == (call_id, "human_joined")

    # The trace: one tree for the call, and each provider callback naming the call it was about.
    [root] = tracer.named("call")
    assert root.attributes == {"call.id": call_id}
    under_the_call = {span.name for span in tracer.spans if span.ancestors()[-1:] == ["call"]}
    assert {
        "call.routing",
        "telephony.answer",
        "speech.open",
        "agent.judgement",
        "call.escalation",
        "telephony.dial",
        "notification.delivery",
        "call.teardown",
        "telephony.terminate",
    } <= under_the_call
    callbacks = tracer.named("telephony.callback")
    assert callbacks
    assert {span.attributes.get("call.id") for span in callbacks} == {call_id}

    # The timeline, from the id alone: every state in order, who was on it, why and how it ended.
    assert [mark["name"] for mark in timeline["marks"] if mark["kind"] == "transition"] == [
        "received",
        "routing",
        "agent_handling",
        "escalation_requested",
        "human_ringing",
        "human_joined",
        "completed",
    ]
    assert [each["role"] for each in timeline["participants"]] == ["agent", "human"]
    assert timeline["escalation"] == {
        "reason": "decision_needs_the_user",
        "status": "ended",
        "delivery": "delivered",
    }
    assert (timeline["outcome"], timeline["live"]) == ("handed_to_user", None)

    # Latency at each boundary the call crossed, as percentiles.
    measured = {each["metric"] for each in measurements["measures"]}
    assert {
        "call.provider_seconds",
        "call.speech_open_seconds",
        "call.judgement_seconds",
        "call.state_seconds",
        "escalation.delivery_seconds",
    } <= measured
    assert all(
        each["p50"] <= each["p90"] <= each["p99"] <= each["maximum"]
        for each in measurements["measures"]
    )
    counted = {
        (each["metric"], tuple(sorted(each["labels"].items()))) for each in measurements["counts"]
    }
    assert ("call.ended", (("outcome", "completed"),)) in counted
    assert set(readiness["dependencies"].values()) == {"closed"}

    # And nothing anywhere in it says who called, who was called, or what anybody said.
    produced = "\n".join(
        [
            *emitted.lines,
            *(json.dumps(span.attributes) for span in tracer.spans),
            json.dumps(live),
            json.dumps(timeline),
            json.dumps(measurements),
            json.dumps(readiness),
        ]
    )
    access_token = account.headers["Authorization"].removeprefix("Bearer ")
    personal = (
        *identifying(DRIVER),
        *identifying(USERS_LINE),
        DRIVER_SAYS,
        ESTABLISHED,
        *DEVICE_TOKENS,
        access_token,
    )
    assert emitted.lines, "no log line was captured"
    assert [each for each in personal if each in produced] == []
    # The words did reach the user, through the push the escalation sent: absent above by design.
    assert any(ESTABLISHED in notification.body for notification in pushes.sent())


@pytest.mark.parametrize("call_id", ["CAsim-never-stored", "%20CAsim-padded"])
async def test_a_call_that_was_never_stored_has_no_timeline(database: str, call_id: str) -> None:
    async with streaming_system(database) as system:
        response = await system.api.http.get(
            f"/diagnostics/calls/{call_id}",
            headers={"Authorization": f"Bearer {DIAGNOSTICS_TOKEN}"},
        )

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
