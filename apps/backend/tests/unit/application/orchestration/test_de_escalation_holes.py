"""A user who does not come after the assistant handed the call over to them."""

from __future__ import annotations

import asyncio

from letmehandle.application.agent.ports import CallEnding
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import ParticipantOutcome
from tests.support.orchestration import (
    WANTS_THE_USER,
    Look,
    StreamingLine,
    eventually,
    orchestrating,
)

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))


async def test_a_busy_user_after_a_hand_over_returns_the_caller_to_the_assistant() -> None:
    line = StreamingLine()
    # A model reaching the user for a caller who asked, and handing over.
    looks = [Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER)]
    async with orchestrating(line, looks=looks) as running:
        line.arrives(CALL, STRANGER)
        await running.settled(CALL, CallState.AGENT_HANDLING)
        await running.session()
        line.assistant_joins(CALL)
        await eventually(lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT))
        await running.caller_says("Can I speak to them, please?")
        await running.settled(CALL, CallState.HUMAN_RINGING)
        # The assistant stays with the caller while the phone rings; a second look waits.
        session = await running.session()
        await running.caller_says("Are they there yet?")
        await eventually(lambda: running.judgements == 2)
        assert not session.is_closed

        line.user_unreachable(CALL, ParticipantOutcome.BUSY)
        await asyncio.sleep(0.1)

        # An unanswered ring returns the call to the assistant with that outcome (D-029).
        assert CallId(CALL) in running.orchestrator._runs
        assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
        assert not session.is_closed
        line.hangs_up(CALL)
        await running.ended(CALL)
