"""Ending a call, held to which kind of ending it is."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.ports import CallEnding
from letmehandle.application.agent.tools.end_call import EndCall
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from tests.support.recording_call_actions import Ended
from tests.unit.application.agent.calls import a_call
from tests.unit.application.agent.kit import Kit, answered, refused

if TYPE_CHECKING:
    from collections.abc import Mapping

MAY_DECLINE = AgentAuthority.granting(Capability.DECLINE_ON_THE_USERS_BEHALF)
EVERYTHING_BUT_DECLINING = AgentAuthority.granting(
    *(each for each in Capability if each is not Capability.DECLINE_ON_THE_USERS_BEHALF)
)


def tool(kit: Kit) -> EndCall:
    return EndCall(kit.notes, kit.actions, kit.escalation)


async def test_a_resolved_call_ends_without_any_grant() -> None:
    kit = Kit()
    call = a_call(authority=AgentAuthority.none())

    said = await answered(tool(kit), call, {"ending": "resolved"})

    assert kit.actions.actions == [Ended(call.call_id, CallEnding.RESOLVED)]
    # The member itself, not its text: orchestration is handed the type it matches on.
    assert kit.actions.of_kind(Ended)[0].ending is CallEnding.RESOLVED
    assert said == "The call has ended."
    assert kit.notes.ended


async def test_declining_a_caller_needs_the_users_grant_to_decline() -> None:
    kit = Kit()
    call = a_call(authority=MAY_DECLINE)

    await answered(tool(kit), call, {"ending": "declined"})

    assert kit.actions.actions == [Ended(call.call_id, CallEnding.DECLINED)]


async def test_without_the_grant_to_decline_the_caller_is_not_turned_away() -> None:
    kit = Kit()

    reason = await refused(
        tool(kit), a_call(authority=EVERYTHING_BUT_DECLINING), {"ending": "declined"}
    )

    assert reason == "the assistant is not authorised to decline something on the user's behalf"
    assert kit.actions.actions == []
    assert not kit.notes.ended


async def test_a_call_is_not_handed_over_to_a_user_who_was_never_reached() -> None:
    kit = Kit()

    reason = await refused(tool(kit), a_call(), {"ending": "handed_over"})

    assert reason == "the user has not been reached for this call, so it was not handed over"
    assert kit.actions.actions == []
    assert not kit.notes.ended


async def test_a_call_the_user_was_reached_for_can_be_handed_over() -> None:
    kit = Kit()
    call = a_call()
    await kit.escalation.consider(
        call, EscalationProposal(importance=CallImportance.URGENT, intent=CallIntent.PERSONAL)
    )

    await answered(tool(kit), call, {"ending": "handed_over"})

    assert kit.actions.of_kind(Ended) == [Ended(call.call_id, CallEnding.HANDED_OVER)]


async def test_a_call_is_ended_once() -> None:
    kit = Kit()
    call = a_call(authority=MAY_DECLINE)
    await answered(tool(kit), call, {"ending": "resolved"})

    reason = await refused(tool(kit), call, {"ending": "declined"})

    assert reason == "the call has already been ended"
    assert kit.actions.of_kind(Ended) == [Ended(call.call_id, CallEnding.RESOLVED)]
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
async def test_a_malformed_ending_is_refused_and_the_call_goes_on(
    arguments: Mapping[str, object], because: str
) -> None:
    kit = Kit()

    reason = await refused(tool(kit), a_call(authority=MAY_DECLINE), arguments)

    assert reason.startswith(because)
    assert kit.actions.actions == []
    assert not kit.notes.ended
