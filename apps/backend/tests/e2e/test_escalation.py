"""Calls on which the assistant needs the user, over the streaming transport.

B: a delivery driver the user has to decide for; the user answers and joins the live call. G: the
user does not answer, and the assistant takes the call back and concludes it. H: the caller gives up
while the user's phone is ringing. And two combinations earlier phases marked as risky: the user
asked for twice on one call, and every notification failing while the ring still goes through.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.notification import DeliveryStatus
from tests.e2e.harness import (
    DRIVER,
    PATIENCE_SECONDS,
    USERS_LINE,
    Emitted,
    Pushes,
    a_user,
    identifying,
    streaming_system,
)
from tests.support.scripted_model import CallTool, assess
from tests.support.simulated_twilio import Answering, eventually

pytestmark = pytest.mark.integration

ESTABLISHED = "A courier has a parcel that needs a signature."
DELIVERY = assess(
    intent="delivery_in_progress",
    importance="notable",
    needs_the_users_decision=True,
    caller_summary=ESTABLISHED,
)
DRIVER_SAYS = "Hello, I have a parcel for this address and it needs a signature."
WANTS_THE_USER = assess(importance="urgent", caller_asked_for_the_user=True)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


async def test_b_a_delivery_driver_reaches_the_user_who_joins_the_live_call(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-delivery"
    async with streaming_system(database, steps=[DELIVERY]) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.ANSWERS

        await system.arrives(account, call_id, caller=DRIVER)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await eventually(lambda: system.transport.open_media_sockets == 1)
        assert system.on_the_call(call_id) == ["caller", "assistant"]
        await system.caller_says(DRIVER_SAYS)
        joined = await system.reaches(account, call_id, CallState.HUMAN_JOINED)

        # Three parties on one call: the caller never moved, the assistant stayed, the user came.
        await eventually(lambda: system.on_the_call(call_id) == ["caller", "assistant", "user"])
        assert [each.role for each in joined.participants] == [
            ParticipantRole.AGENT,
            ParticipantRole.HUMAN,
        ]
        agent, human = joined.participants
        assert joined.escalated_at is not None
        assert agent.joined_at <= joined.escalated_at <= human.joined_at
        assert agent.is_present
        assert human.is_present
        assert not system.dialled()[0].muted
        # The assistant still hears the caller and is heard, and knows the user is on the call.
        assistant = system.provider.assistant_of(call_id)
        heard = len(assistant.sent_to_call)
        await system.provider.send_caller_audio(call_id, b"\x22" * 160, frames=2)
        await eventually(lambda: len(assistant.sent_to_call) >= heard + 2)
        session = system.session()
        await eventually(lambda: any("on_the_call" in each for each in session.context_updates))

        # One notification per phone, carrying why, who and what is known, bound to the call.
        await eventually(lambda: len(pushes.sent()) == 2)
        for notification in pushes.sent():
            assert notification.call_id == CallId(call_id)
            assert notification.title == "There is a decision only you can make"
            assert notification.caller_label == "Unknown caller"
            assert notification.body == f"So far: {ESTABLISHED}"
            assert notification.data["reason"] == "decision_needs_the_user"
        live = await system.escalation_when(
            account, call_id, lambda escalation: escalation["delivery"] == "delivered"
        )
        assert live["status"] == "live"
        assert live["reason"] == "decision_needs_the_user"
        assert live["established"] == ESTABLISHED

        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "handed_to_user"
        assert detail["human_joined"] is True
        assert detail["handling"] == "assistant"
        assert detail["escalation_reason"] == "decision_needs_the_user"
        assert detail["intent"] == "delivery_in_progress"
        timings = detail["timings"]
        assert (
            at(timings["received_at"])
            <= at(timings["escalated_at"])
            <= at(timings["human_joined_at"])
            <= at(timings["ended_at"])
        )
        ended = await system.api.escalation(account, call_id)
        assert ended is not None
        assert ended["status"] == "ended"
        assert ended["delivery"] == "delivered"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        assert len(system.dialled()) == 1
        assert len(pushes.sent()) == 2
        assert system.model.unused_steps == 0
        await system.released()
        assert emitted.mentions(DRIVER_SAYS, *identifying(DRIVER)) == []


async def test_g_an_unanswered_escalation_returns_the_call_to_the_assistant(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-unanswered"
    concluding = assess(intent="delivery_in_progress", importance="routine")
    steps = [DELIVERY, CallTool("end_call", {"ending": "resolved"}), concluding]
    async with streaming_system(database, steps=steps) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.RINGS_OUT

        await system.arrives(account, call_id, caller=DRIVER)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says(DRIVER_SAYS)
        session = system.session()
        # The assistant is told the user did not answer, and has the call again.
        await eventually(lambda: any("no_answer" in each for each in session.context_updates))
        handed_back = await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        assert handed_back.escalated_at is not None
        assert system.on_the_call(call_id) == ["caller", "assistant"]
        assistant = system.provider.assistant_of(call_id)
        heard = len(assistant.sent_to_call)
        await system.provider.send_caller_audio(call_id, b"\x33" * 160, frames=2)
        await eventually(lambda: len(assistant.sent_to_call) >= heard + 2)

        await system.caller_says("Never mind, I will leave it with the neighbour.")
        detail = await system.ended(account, call_id)

        # The assistant concluded it: the call ended on this side, not by the caller hanging up.
        assert system.provider.conference_of(call_id).ended
        assert detail["outcome"] == "unanswered_escalation"
        assert detail["human_joined"] is False
        assert detail["escalation_reason"] == "decision_needs_the_user"
        assert detail["timings"]["escalated_at"] is not None
        assert detail["timings"]["human_joined_at"] is None
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        assert [each.role for each in call.participants] == [ParticipantRole.AGENT]
        [ring] = system.dialled()
        assert ring.finished
        assert not ring.answered
        assert len(pushes.sent()) == 2
        escalation = await system.api.escalation(account, call_id)
        assert escalation is not None
        assert escalation["status"] == "ended"
        assert system.model.unused_steps == 0
        await system.released()
        assert emitted.mentions(DRIVER_SAYS, *identifying(DRIVER)) == []


async def test_h_the_caller_hanging_up_during_the_escalation_stops_the_ring(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-gave-up"
    async with streaming_system(database, steps=[WANTS_THE_USER]) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.KEEPS_RINGING

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says("Can I speak to her, please?")
        await system.reaches(account, call_id, CallState.HUMAN_RINGING)
        [ring] = system.dialled()
        assert not ring.finished
        await eventually(lambda: len(pushes.sent()) == 2)

        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        # Nothing is left ringing for a call that no longer exists.
        await eventually(lambda: ring.finished, seconds=PATIENCE_SECONDS)
        assert not ring.answered
        assert not ring.in_conference
        assert system.provider.conference_of(call_id).ended
        assert detail["outcome"] == "unanswered_escalation"
        assert detail["human_joined"] is False
        assert detail["escalation_reason"] == "caller_asked_for_the_user"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        assert [each.role for each in call.participants] == [ParticipantRole.AGENT]
        # The app, opened from the notification now, shows what happened rather than a call to join.
        escalation = await system.api.escalation(account, call_id)
        assert escalation is not None
        assert escalation["status"] == "ended"
        await system.released()
        assert emitted.mentions("Can I speak to her, please?") == []


async def test_escalation_asked_for_twice_rings_the_user_once(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-twice"
    async with streaming_system(database, steps=[WANTS_THE_USER, WANTS_THE_USER]) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.KEEPS_RINGING

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says("I need to talk to her.")
        await system.reaches(account, call_id, CallState.HUMAN_RINGING)
        await system.caller_says("Please, it really is urgent, get her now.")
        await eventually(lambda: system.model.unused_steps == 0)
        await eventually(lambda: len(pushes.sent()) == 2)

        still = await system.stored(account, call_id)
        assert still is not None
        assert still.state is CallState.HUMAN_RINGING
        assert len(system.dialled()) == 1
        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        assert detail["escalation_reason"] == "caller_asked_for_the_user"
        assert len(system.dialled()) == 1
        # One notification per phone for the call, however often the caller insisted.
        assert len(pushes.ios.sent) == 1
        assert len(pushes.android.sent) == 1
        await system.released()
        assert emitted.mentions("I need to talk to her.") == []


async def test_a_failing_notification_does_not_stop_the_escalation(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-push-down"
    pushes.ios.status = DeliveryStatus.FAILED
    pushes.android.status = DeliveryStatus.FAILED
    async with streaming_system(database, steps=[WANTS_THE_USER]) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.ANSWERS

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says("Is she available? It is urgent.")
        await system.reaches(account, call_id, CallState.HUMAN_JOINED)
        # The app surfaces that it could not be told, and can still read what it would have said.
        failed = await system.escalation_when(
            account, call_id, lambda escalation: escalation["delivery"] == "failed"
        )
        assert failed["reason"] == "caller_asked_for_the_user"
        assert failed["status"] == "live"

        await system.provider.user_hangs_up(account.number)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "handed_to_user"
        assert detail["human_joined"] is True
        assert system.provider.conference_of(call_id).ended
        await system.released()
        assert emitted.mentions("Is she available? It is urgent.") == []
