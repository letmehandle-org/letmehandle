"""T7: escalation on a streaming call that cannot bridge keeps the assistant on it (D-029)."""

from __future__ import annotations

import pytest

from letmehandle.domain.models.call_state import CallState
from tests.e2e.harness import (
    CALLER,
    USERS_LINE,
    Emitted,
    Pushes,
    a_user,
    identifying,
    streaming_system,
)
from tests.support.scripted_model import assess
from tests.support.simulated_twilio import Answering, eventually

pytestmark = pytest.mark.integration

WANTS_THE_USER = assess(importance="urgent", caller_asked_for_the_user=True)


async def test_t7_escalation_where_nobody_can_be_added_takes_the_handoff_the_transport_has(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    call_id = "CAsim-e2e-cannot-bridge"
    asked = "I need to speak to her in person, please put her on."
    steps = [WANTS_THE_USER, WANTS_THE_USER]
    async with streaming_system(database, steps=steps, without_bridging=True) as system:
        account = await a_user(system)
        system.provider.answering[USERS_LINE] = Answering.ANSWERS

        await system.arrives(account, call_id)
        await system.reaches(account, call_id, CallState.AGENT_HANDLING)
        await system.caller_says(asked)
        await system.caller_says("Hello? Is she coming?")
        # The second look starts only once the first one's request has been acted on.
        await eventually(lambda: system.model.unused_steps == 0)
        assistant = await system.provider.assistant_of(call_id)
        await system.provider.send_caller_audio(call_id, b"\x55" * 160, frames=2)
        await eventually(lambda: len(assistant.sent_to_call) >= 2)

        still = await system.stored(account, call_id)
        assert still is not None
        assert still.state is CallState.AGENT_HANDLING
        assert still.escalated_at is None
        assert system.dialled() == []
        assert system.on_the_call(call_id) == ["caller", "assistant"]

        await system.provider.caller_hangs_up(call_id)
        detail = await system.ended(account, call_id)

        assert detail["escalation_reason"] == "caller_asked_for_the_user"
        assert detail["outcome"] == "unanswered_escalation"
        assert detail["human_joined"] is False
        assert detail["timings"]["escalated_at"] is None
        # Nothing rang, so there is no ring for a notification to supplement (D-016).
        assert await system.api.escalation(account, call_id) is None
        assert pushes.sent() == []
        await system.released()
        assert emitted.mentions(asked, *identifying(CALLER)) == []
