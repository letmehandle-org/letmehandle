"""Endings that leave something behind: a teardown cut short, and records written after it.

Each test here reproduces a defect and is expected to fail until it is fixed.
"""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.support.orchestration import (
    Running,
    StreamingLine,
    eventually,
    orchestrating,
)

CALL = "call"
STRANGER = Caller(number=PhoneNumber("+12025550101"))


async def with_the_assistant(running: Running) -> StreamingLine:
    line = running.line
    assert isinstance(line, StreamingLine)
    line.arrives(CALL, STRANGER)
    await running.settled(CALL, CallState.AGENT_HANDLING)
    await running.session()
    line.assistant_joins(CALL)
    await eventually(lambda: running.stores.call(CALL).has_participant(ParticipantRole.AGENT))
    return line


@pytest.mark.xfail(
    strict=True,
    reason="a speech session whose close raises aborts _finish: no terminate, no summary",
)
async def test_a_speech_session_that_fails_to_close_still_ends_the_call() -> None:
    line = StreamingLine()
    async with orchestrating(line) as running:
        await with_the_assistant(running)
        session = await running.session()

        async def close() -> None:
            # What the real sessions do when their reader task died of a defect.
            raise ProviderError("speech", "the reader failed", retryable=False)

        session.close = close  # type: ignore[method-assign]  # a session that fails as it closes
        line.hangs_up(CALL)
        await eventually(lambda: CallId(CALL) not in running.orchestrator._runs)

        assert line.asked("terminate", CALL) == 1
        assert CallId(CALL) in running.stores.summaries.stored
        assert running.stores.call(CALL).state is CallState.COMPLETED
