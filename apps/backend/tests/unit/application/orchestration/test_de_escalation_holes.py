"""A user who does not come after the assistant handed the call over to them.

Each test here reproduced a defect before its fix, and keeps it fixed.
"""

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
    # What a model does when a caller asks for the user: reach them, and say it is handing over.
    looks = [Look(proposal=WANTS_THE_USER, ending=CallEnding.HANDED_OVER)]
    async with orchestrating(line, looks=looks) as running:
        line.arrives(CALL, STRANGER)
        await running.settled(CALL, CallState.AGENT_HANDLING)
        await running.session()
        line.assistant_joins(CALL)
        await eventually(lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT))
        await running.caller_says("Can I speak to them, please?")
        await running.settled(CALL, CallState.HUMAN_RINGING)
        # The hand-over leaves the assistant with the caller while the phone rings. A second look
        # starts only once the first, and the ending it asked for, are done with.
        session = await running.session()
        await running.caller_says("Are they there yet?")
        await eventually(lambda: running.judgements == 2)
        assert not session.is_closed

        line.user_unreachable(CALL, ParticipantOutcome.BUSY)
        await asyncio.sleep(0.1)

        # D-029: an unanswered, busy, failed or machine-answered ring returns the call to the
        # assistant with that outcome in its context, rather than ending the call.
        assert CallId(CALL) in running.orchestrator._runs
        assert running.stores.call(CALL).state is CallState.AGENT_HANDLING
        assert not session.is_closed
        line.hangs_up(CALL)
        await running.ended(CALL)
