"""Asking to end a call, held to which kind of ending it is.

The tool only writes the ending down. Whether it is applied, once the model has finished, is the
conclusion's to decide and is tested beside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.ports import CallEnding
from letmehandle.application.agent.tools.end_call import EndCall
from letmehandle.domain.models.authority import AgentAuthority, Capability
from tests.unit.application.agent.calls import a_call
from tests.unit.application.agent.kit import Kit, answered, refused

if TYPE_CHECKING:
    from collections.abc import Mapping

MAY_DECLINE = AgentAuthority.granting(Capability.DECLINE_ON_THE_USERS_BEHALF)
EVERYTHING_BUT_DECLINING = AgentAuthority.granting(
    *(each for each in Capability if each is not Capability.DECLINE_ON_THE_USERS_BEHALF)
)


def tool(kit: Kit) -> EndCall:
    return EndCall(kit.notes)


@pytest.mark.parametrize("ending", [CallEnding.RESOLVED, CallEnding.HANDED_OVER])
async def test_an_ending_that_needs_no_grant_is_written_down_and_nothing_else(
    ending: CallEnding,
) -> None:
    kit = Kit()

    said = await answered(tool(kit), a_call(authority=AgentAuthority.none()), {"ending": ending})

    # The member itself, not its text: the conclusion hands orchestration the type it matches on.
    assert kit.notes.requested_ending is ending
    assert kit.actions.actions == []
    assert not kit.notes.ended
    assert said == (
        "The call will end once your assessment is recorded, if the user's rules still allow it "
        "then."
    )


async def test_declining_a_caller_is_written_down_with_the_users_grant_to_decline() -> None:
    kit = Kit()

    await answered(tool(kit), a_call(authority=MAY_DECLINE), {"ending": "declined"})

    assert kit.notes.requested_ending is CallEnding.DECLINED


async def test_without_the_grant_to_decline_the_caller_is_not_turned_away() -> None:
    kit = Kit()

    reason = await refused(
        tool(kit), a_call(authority=EVERYTHING_BUT_DECLINING), {"ending": "declined"}
    )

    assert reason == "the assistant is not authorised to decline something on the user's behalf"
    assert kit.notes.requested_ending is None


async def test_one_ending_is_asked_for() -> None:
    kit = Kit()
    call = a_call(authority=MAY_DECLINE)
    await answered(tool(kit), call, {"ending": "resolved"})

    reason = await refused(tool(kit), call, {"ending": "declined"})

    assert reason == "an ending has already been asked for"
    assert kit.notes.requested_ending is CallEnding.RESOLVED
    assert [refusal.reason for refusal in kit.notes.refusals] == [reason]


@pytest.mark.parametrize(
    ("arguments", "because"),
    [
        ({}, "ending is required"),
        ({"ending": "hung_up"}, "ending must be one of: resolved, handed_over, declined"),
        ({"ending": "RESOLVED"}, "ending must be one of"),
        ({"ending": 0}, "ending must be one of"),
        ({"ending": "resolved", "reason": "the caller was rude"}, "unexpected arguments: reason"),
        (
            {"ending": "declined", "capability": "decline_on_the_users_behalf"},
            "unexpected arguments",
        ),
    ],
)
async def test_a_malformed_ending_is_refused_and_nothing_is_asked_for(
    arguments: Mapping[str, object], because: str
) -> None:
    kit = Kit()

    reason = await refused(tool(kit), a_call(authority=MAY_DECLINE), arguments)

    assert reason.startswith(because)
    assert kit.notes.requested_ending is None
