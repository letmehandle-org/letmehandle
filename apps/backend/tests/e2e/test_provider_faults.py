"""Provider faults: F duplicated and reordered callbacks, the model down, and a restart."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from tests.e2e.app_client import call_handling
from tests.e2e.harness import (
    CALLER,
    IMPORTANT_CALLER,
    USERS_LINE,
    Emitted,
    Pushes,
    a_user,
    identifying,
    streaming_system,
)
from tests.support.scripted_model import Fail, assess
from tests.support.simulated_twilio import Answering, SimulatedTwilio, eventually

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.support.simulated_twilio import Delivery

pytestmark = pytest.mark.integration

WANTS_THE_USER = assess(importance="urgent", caller_asked_for_the_user=True)
MODEL_DOWN = Fail(ProviderError("model", "the endpoint is unreachable", retryable=True))


def backwards_and_twice(held: list[Delivery]) -> list[Delivery]:
    """Every held callback, newest first, and then all of them again with the same tokens."""
    # Nothing held would make the scenario an ordinary call that proves nothing about order.
    assert len(held) > 1
    return list(reversed(held)) * 2


async def test_f_duplicated_and_reordered_callbacks_leave_one_correct_call(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-unruly-callbacks"
    async with streaming_system(database, steps=[WANTS_THE_USER]) as system:
        account = await a_user(system)
        provider = system.provider
        provider.answering[USERS_LINE] = Answering.ANSWERS
        provider.duplicate_callbacks = True

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.assistant_is_streaming(call_id)

        # The user's phone is dialled and answers, and every callback saying so is held back.
        provider.hold()
        await system.caller_says("Put her on, please.")
        await system.reaches(account, call_id, CallState.HUMAN_RINGING)
        await eventually(system.user_is_on_the_call)
        await provider.settle(system.transport)
        await provider.release(backwards_and_twice)
        joined = await system.joined_by_the_user(account, call_id)
        assert [each.role for each in joined.participants] == [
            ParticipantRole.AGENT,
            ParticipantRole.HUMAN,
        ]

        # The caller hangs up, and the end of the call arrives the same way.
        provider.hold()
        await provider.caller_hangs_up(call_id)
        await provider.settle(system.transport)
        await provider.release(backwards_and_twice)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "handed_to_user"
        assert detail["human_joined"] is True
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        # One of each, however often and in whatever order they were heard of.
        assert [each.role for each in call.participants] == [
            ParticipantRole.AGENT,
            ParticipantRole.HUMAN,
        ]
        assert len(system.dialled()) == 1
        assert len(pushes.ios.sent) == 1
        assert len(pushes.android.sent) == 1
        assert {delivery.status for delivery in provider.delivered} <= {200, 204}
        assert [each["id"] for each in await system.api.calls(account)] == [call_id]
        await system.released()
        assert emitted.mentions(*identifying(CALLER)) == []


def the_assistants_leg_first(call_id: str) -> Callable[[list[Delivery]], list[Delivery]]:
    """Every held callback about the assistant's leg, then the caller's and the conference's."""

    def order(held: list[Delivery]) -> list[Delivery]:
        assistant = [each for each in held if _leg_of(each) not in {None, call_id}]
        caller = [each for each in held if each not in assistant]
        # Both sides held, or the order would prove nothing.
        assert assistant
        assert caller
        return assistant + caller

    return order


def _leg_of(delivery: Delivery) -> str | None:
    return dict(delivery.params).get("CallSid")


async def test_a_caller_hanging_up_is_a_hang_up_when_the_assistants_leg_is_heard_of_first(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-hang-up-heard-late"
    async with streaming_system(database, steps=[]) as system:
        account = await a_user(system)
        provider = system.provider
        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.assistant_is_streaming(call_id)

        # The assistant's stream stops at once and its leg's callbacks arrive before the caller's.
        provider.hold()
        await provider.caller_hangs_up(call_id)
        await provider.settle(system.transport)
        await provider.release(the_assistants_leg_first(call_id))
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "caller_hung_up"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        await system.released()


async def test_the_model_unavailable_at_a_judgement_leaves_the_caller_with_the_assistant(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-model-down"
    async with streaming_system(database, steps=[MODEL_DOWN] * 8) as system:
        account = await a_user(system)

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says("Hello, is anybody there?")
        await eventually(lambda: bool(system.model.requests))
        # Still answered and still heard: a model that fails is not a reason to hang up.
        assistant = await system.provider.assistant_of(call_id)
        await system.provider.send_caller_audio(call_id, b"\x66" * 160, frames=2)
        await eventually(lambda: len(assistant.sent_to_call) >= 2)
        still = await system.stored(account, call_id)
        assert still is not None
        assert still.state is CallState.AGENT_HANDLING
        # A stranger nobody could understand does not wake the user.
        assert system.dialled() == []

        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "caller_hung_up"
        assert detail["intent"] == "undetermined"
        assert pushes.sent() == []
        await system.released()
        assert emitted.mentions("Hello, is anybody there?", *identifying(CALLER)) == []


async def test_the_model_unavailable_still_reaches_the_user_for_an_important_contact(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-model-down-contact"
    contact = {"phone_number": IMPORTANT_CALLER, "label": "Sister", "posture": "handle_with_agent"}
    async with streaming_system(database, steps=[MODEL_DOWN] * 8) as system:
        account = await a_user(
            system, preferences={**call_handling(), "important_contacts": [contact]}
        )
        system.provider.answering[USERS_LINE] = Answering.ANSWERS

        await system.arrives(account, call_id, caller=IMPORTANT_CALLER)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says("It's me, pick up.")
        await system.joined_by_the_user(account, call_id)
        escalation = await system.escalation_when(
            account, call_id, lambda each: each["delivery"] == "delivered"
        )
        assert escalation["reason"] == "cannot_understand_the_caller"
        assert escalation["caller_label"] == "Sister"

        await system.provider.user_hangs_up(account.number)
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "handed_to_user"
        assert detail["escalation_reason"] == "cannot_understand_the_caller"
        await system.released()
        assert emitted.mentions("It's me, pick up.", *identifying(IMPORTANT_CALLER)) == []


async def test_a_restart_mid_call_ends_the_call_for_the_caller_and_records_it(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    live_call = "CAsim-e2e-before-restart"
    next_call = "CAsim-e2e-after-restart"
    provider = SimulatedTwilio()
    try:
        async with streaming_system(database, provider=provider) as before:
            account = await a_user(before)
            await before.arrives(account, live_call)
            await before.reaches(account, live_call, CallState.AGENT_HANDLING)
            await before.assistant_is_streaming(live_call)
            stopping = before.transport
        # The process has stopped. Its transport ended the call where the caller is.
        await provider.settle(stopping)
        assert provider.conference_of(live_call).ended
        assert not [leg for leg in provider.legs.values() if leg.in_conference]

        async with streaming_system(database, provider=provider) as after:
            detail = await after.ended(account, live_call)
            assert detail["outcome"] == "failed"
            assert detail["status"] == "ended"
            call = await after.stored(account, live_call)
            assert call is not None
            assert call.state is CallState.FAILED
            assert call.ended_at is not None

            # The process that started in its place takes calls as if nothing had happened.
            await after.arrives(account, next_call)
            await after.reaches(account, next_call, CallState.AGENT_HANDLING)
            await provider.caller_hangs_up(next_call)
            fresh = await after.ended(account, next_call)
            assert fresh["outcome"] == "caller_hung_up"
            assert {each["id"] for each in await after.api.calls(account)} == {
                live_call,
                next_call,
            }
            await after.released()
    finally:
        await provider.close()
    assert emitted.mentions(*identifying(CALLER)) == []
