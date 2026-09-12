"""Whole calls, end to end, against the simulated provider over real HTTP and websockets.

Each test runs the application on loopback, lets the simulated provider place a call into it,
and watches what the orchestrator would hear. Every termination path is checked by counting what
is left afterwards — calls held, media sockets open, tasks running — because a transport that
reports a call ended and keeps a socket open is the failure that takes a service down slowly.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.transport.twilio import transport as transport_module
from letmehandle.application.speech.conversation import Conversation, ConversationEnd, Transcript
from letmehandle.domain.errors import IllegalTransitionError
from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND, AudioFrame
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallEventKind,
    answering,
    audio_streaming,
    bridging,
    three_way,
)
from letmehandle.domain.ports.speech import SpeechStarted
from tests.contracts.fakes import EchoSpeechProvider, EchoSpeechSession, FixedClock
from tests.support.recording_metrics import RecordingMetrics
from tests.support.simulated_twilio import (
    SIMULATED_ACCOUNT,
    USER_NUMBER,
    Answering,
    Delivery,
    Deployment,
    eventually,
    simulated_deployment,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

CALL = CallId("CAsim-caller")


@pytest.fixture
async def deployment(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Deployment]:
    monkeypatch.setattr(transport_module, "LATE_CALLBACK_GRACE_SECONDS", 0.05)
    async with simulated_deployment() as running:
        yield running
        await running.settle()
        assert_released(running)


def assert_released(deployment: Deployment) -> None:
    transport = deployment.transport
    assert transport.pending_tasks == 0


def assert_nothing_held(deployment: Deployment) -> None:
    transport = deployment.transport
    assert transport.active_calls == 0
    assert transport.open_media_sockets == 0
    assert transport.pending_tasks == 0


async def answered(deployment: Deployment) -> None:
    """A call arrived, the assistant joined and its stream is up."""
    await deployment.provider.place_call(CALL.value)
    await answering(deployment.transport).answer(CALL)
    await deployment.settle()


# ------------------------------------------------------------------------------ answering


async def test_an_inbound_call_is_answered_and_the_assistant_joins_it(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    assert deployment.kinds() == [
        ("incoming", None, None),
        ("answered", None, None),
        ("participant_joined", "assistant", None),
    ]
    incoming = deployment.events[0]
    assert incoming.caller is not None
    assert incoming.caller.number is not None
    assert deployment.transport.open_media_sockets == 1
    conference = deployment.provider.conference_of(CALL.value)
    assert [leg.label for leg in conference.legs] == ["caller", "assistant-1"]
    assert conference.started


async def test_media_flows_both_ways_and_an_interruption_clears_the_line(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    streaming = audio_streaming(deployment.transport)
    await deployment.provider.send_caller_audio(CALL.value, b"\x11" * 160, frames=3)
    heard = []
    async for frame in streaming.stream_audio(CALL):
        heard.append(frame)
        if len(heard) == 3:
            break
    assert all(frame == AudioFrame(b"\x11" * 160, TELEPHONY_NARROWBAND) for frame in heard)

    await streaming.inject_audio(CALL, AudioFrame(b"\x22" * 160, TELEPHONY_NARROWBAND))
    await streaming.audio_sink(CALL).discard()
    await deployment.settle()
    leg = deployment.provider.assistant_of(CALL.value)
    assert leg.sent_to_call == [b"\x22" * 160]
    assert leg.clears == 1


async def test_a_keypad_press_on_the_stream_changes_nothing(deployment: Deployment) -> None:
    await answered(deployment)
    await deployment.provider.press_digit(CALL.value, "5")
    await deployment.settle()
    assert deployment.transport.open_media_sockets == 1


async def test_a_conversation_runs_over_the_call_as_it_runs_over_a_microphone(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    streaming = audio_streaming(deployment.transport)
    session = await EchoSpeechProvider().connect(
        system_context="a call",
        voice_id="calm",
        locale="en",
        input_format=streaming.audio_format(),
    )
    assert isinstance(session, EchoSpeechSession)
    conversation = Conversation(
        session=session,
        source=streaming.audio_source(CALL),
        sink=streaming.audio_sink(CALL),
        transcript=Transcript(),
        metrics=RecordingMetrics(),
        clock=FixedClock(),
    )
    running = asyncio.create_task(conversation.run())
    await deployment.provider.send_caller_audio(CALL.value, b"\x33" * 160, frames=4)
    leg = deployment.provider.assistant_of(CALL.value)
    await eventually(lambda: len(leg.sent_to_call) >= 4)
    # The echo session says back exactly what it heard, onto the call, through the sink.
    assert leg.sent_to_call == [b"\x33" * 160] * 4

    await session.emit(SpeechStarted(by_caller=True))
    await eventually(lambda: leg.clears >= 1)

    await deployment.provider.caller_hangs_up(CALL.value)
    assert await running is ConversationEnd.SPEAKER_GONE
    await session.close()
    await deployment.settle()
    assert deployment.kinds()[-1] == ("ended", None, None)
    assert_nothing_held(deployment)


# ------------------------------------------------------------------------------- the user


async def test_the_user_is_added_to_the_live_call_and_removed_leaving_it_standing(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    deployment.provider.answering[USER_NUMBER.value] = Answering.ANSWERS
    await bridging(deployment.transport).add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_joined", "user", "answered")
    conference = deployment.provider.conference_of(CALL.value)
    assert [leg.label for leg in conference.legs if leg.in_conference] == [
        "caller",
        "assistant-1",
        "user-2",
    ]

    call = three_way(deployment.transport)
    await call.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    assistant = deployment.provider.assistant_of(CALL.value)
    assert assistant.muted
    await call.set_assistant_presence(CALL, AssistantPresence.SPEAK_TO_USER_ONLY)
    assert not assistant.muted
    assert assistant.coaching == deployment.provider.user_leg(USER_NUMBER).call_sid

    await bridging(deployment.transport).remove_participant(CALL, USER_NUMBER)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_left", "user", None)
    # The call stands, and with nobody else on it the assistant is audible to the caller again.
    assert deployment.transport.active_calls == 1
    assert assistant.coaching is None
    assert not assistant.muted


async def test_the_assistant_can_leave_the_caller_and_user_talking(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    deployment.provider.answering[USER_NUMBER.value] = Answering.ANSWERS
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    await deployment.transport.set_assistant_presence(CALL, AssistantPresence.LEAVE)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_left", "assistant", None)
    assert deployment.transport.open_media_sockets == 0
    with pytest.raises(IllegalTransitionError):
        await deployment.transport.set_assistant_presence(CALL, AssistantPresence.STAY)
    conference = deployment.provider.conference_of(CALL.value)
    assert [leg.label for leg in conference.legs if leg.in_conference] == ["caller", "user-2"]


@pytest.mark.parametrize(
    ("answering", "outcome"),
    [
        (Answering.RINGS_OUT, "no_answer"),
        (Answering.BUSY, "busy"),
        (Answering.FAILS, "failed"),
        (Answering.VOICEMAIL, "answered_by_machine"),
    ],
)
async def test_a_user_who_does_not_join_is_reported_and_the_caller_is_not_left_alone(
    deployment: Deployment, answering: Answering, outcome: str
) -> None:
    await answered(deployment)
    deployment.provider.answering[USER_NUMBER.value] = answering
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    assert ("participant_unreachable", "user", outcome) in deployment.kinds()
    assert not any(
        kind == "participant_joined" and who == "user" for kind, who, _ in deployment.kinds()
    )
    # The assistant is still with the caller, streaming.
    assert deployment.transport.open_media_sockets == 1
    assert not deployment.provider.user_leg(USER_NUMBER).in_conference


# ---------------------------------------------------------------------- how calls end


async def test_the_caller_hanging_up_first_releases_everything(deployment: Deployment) -> None:
    await answered(deployment)
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    await deployment.provider.caller_hangs_up(CALL.value)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("ended", None, None)
    assert [event.kind for event in deployment.events].count(CallEventKind.ENDED) == 1
    # The user was still ringing; the dial is cancelled rather than joining an empty call.
    assert deployment.provider.user_leg(USER_NUMBER).finished
    assert_nothing_held(deployment)


async def test_the_user_hanging_up_first_leaves_the_caller_with_the_assistant(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    deployment.provider.answering[USER_NUMBER.value] = Answering.ANSWERS
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    await deployment.transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await deployment.provider.user_hangs_up(USER_NUMBER)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_left", "user", None)
    assert deployment.transport.active_calls == 1
    assert not deployment.provider.assistant_of(CALL.value).muted


async def test_the_assistants_socket_dropping_mid_call_is_reported_and_recoverable(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    await deployment.provider.drop_assistant_socket(CALL.value)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_left", "assistant", None)
    assert deployment.transport.open_media_sockets == 0
    assert [frame async for frame in deployment.transport.stream_audio(CALL)] == []
    # The caller is still there; answering again brings a new assistant.
    await answering(deployment.transport).answer(CALL)
    await deployment.settle()
    assert deployment.kinds()[-1] == ("participant_joined", "assistant", None)
    assert deployment.transport.open_media_sockets == 1


async def test_a_stream_stopped_without_its_socket_closing_is_closed_from_this_side(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    await deployment.provider.stop_stream_without_closing(CALL.value)
    await deployment.settle()
    assert deployment.transport.open_media_sockets == 0
    assert deployment.kinds()[-1] == ("participant_left", "assistant", None)


async def test_terminating_ends_the_call_everywhere_and_twice_is_safe(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    deployment.provider.answering[USER_NUMBER.value] = Answering.ANSWERS
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    await deployment.transport.terminate(CALL)
    await deployment.transport.terminate(CALL)
    await deployment.settle()
    conference = deployment.provider.conference_of(CALL.value)
    assert conference.ended
    assert not any(leg.in_conference for leg in conference.legs)
    assert [event.kind for event in deployment.events].count(CallEventKind.ENDED) == 1
    assert_nothing_held(deployment)


async def test_terminating_a_call_whose_conference_never_started(deployment: Deployment) -> None:
    await deployment.provider.place_call(CALL.value)
    await deployment.settle()
    await deployment.transport.terminate(CALL)
    await deployment.settle()
    assert deployment.provider.legs[CALL.value].finished
    assert_nothing_held(deployment)


# -------------------------------------------------------------- what the provider gets wrong


async def test_duplicated_callbacks_are_inert(deployment: Deployment) -> None:
    provider = deployment.provider
    provider.hold()
    await provider.place_call(CALL.value)
    await deployment.settle()
    held = list(provider.held or [])
    await provider.release()
    # The same deliveries again, with their own tokens, and once more with fresh ones.
    for delivery in held:
        await provider.deliver(delivery)
        await provider.deliver(
            Delivery(delivery.path_and_query, delivery.params, token=delivery.token + "-retry")
        )
    await deployment.settle()
    assert deployment.kinds() == [("incoming", None, None), ("answered", None, None)]
    assert {delivery.status for delivery in provider.delivered} == {204}


async def test_reordered_callbacks_are_resolved_by_state_not_arrival(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    provider = deployment.provider
    provider.answering[USER_NUMBER.value] = Answering.ANSWERS
    provider.hold()
    await deployment.transport.add_participant(CALL, USER_NUMBER)
    await deployment.settle()
    await provider.user_hangs_up(USER_NUMBER)
    await deployment.settle()
    await provider.release(lambda held: list(reversed(held)))
    await deployment.settle()
    user_events = [shape for shape in deployment.kinds() if shape[1] == "user"]
    # The leave came first and settled it; the join that arrived after it was already stale.
    assert user_events == [("participant_left", "user", None)]


async def test_a_dropped_conference_end_still_ends_the_call_by_the_callers_leaving(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    provider = deployment.provider
    provider.hold()
    await provider.caller_hangs_up(CALL.value)
    await deployment.settle()
    await provider.release(
        lambda held: [
            each for each in held if ("StatusCallbackEvent", "conference-end") not in each.params
        ]
    )
    await deployment.settle()
    assert deployment.kinds()[-1] == ("ended", None, None)
    assert_nothing_held(deployment)


async def test_a_dropped_caller_leave_still_ends_the_call_by_the_conference_ending(
    deployment: Deployment,
) -> None:
    await answered(deployment)
    provider = deployment.provider
    provider.hold()
    await provider.caller_hangs_up(CALL.value)
    await deployment.settle()
    await provider.release(
        lambda held: [
            each
            for each in held
            if not (
                ("StatusCallbackEvent", "participant-leave") in each.params
                and ("ParticipantLabel", "caller") in each.params
            )
        ]
    )
    await deployment.settle()
    assert deployment.kinds()[-1] == ("ended", None, None)
    assert_nothing_held(deployment)


async def test_a_forged_callback_is_refused_and_changes_nothing(deployment: Deployment) -> None:
    await answered(deployment)
    response = await deployment.provider.post_signed(
        f"/telephony/conference/status?call={CALL.value}",
        [
            ("AccountSid", SIMULATED_ACCOUNT),
            ("ConferenceSid", deployment.provider.conference_of(CALL.value).sid),
            ("StatusCallbackEvent", "conference-end"),
            ("SequenceNumber", "999"),
        ],
        token="a-guessed-token",
    )
    assert response.status_code == 403
    await deployment.settle()
    assert deployment.transport.active_calls == 1


async def test_a_forged_media_handshake_is_refused(deployment: Deployment) -> None:
    await deployment.provider.place_call(CALL.value)
    # Signing the handshake over another URL is as good as not signing it.
    deployment.provider.sign_handshake_url = "wss://elsewhere.example.com/telephony/media"
    await answering(deployment.transport).answer(CALL)
    await deployment.settle()
    assert deployment.provider.handshake_statuses == [403]
    assert deployment.transport.open_media_sockets == 0
    assert deployment.kinds()[-1] == ("participant_unreachable", "assistant", "failed")


async def test_a_handshake_signed_with_a_trailing_slash_is_accepted(
    deployment: Deployment,
) -> None:
    deployment.provider.sign_handshake_with_slash = True
    await answered(deployment)
    assert deployment.transport.open_media_sockets == 1
