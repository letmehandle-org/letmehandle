"""Taking a message: only when the user allows it, only a message, and only once it is valid."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.tools.message import MAX_MESSAGE_CHARACTERS, TakeAMessage
from letmehandle.domain.models.authority import AgentAuthority, Capability
from tests.support.recording_call_actions import MessageTaken
from tests.unit.application.agent.calls import a_call
from tests.unit.application.agent.kit import Kit, answered, refused

if TYPE_CHECKING:
    from collections.abc import Mapping

MAY_TAKE_A_MESSAGE = AgentAuthority.granting(Capability.TAKE_A_MESSAGE)


async def test_a_message_is_kept_when_the_user_allows_messages() -> None:
    kit = Kit()
    call = a_call(authority=MAY_TAKE_A_MESSAGE)

    said = await answered(
        TakeAMessage(kit.notes, kit.actions), call, {"message": "  Call the garage back.  "}
    )

    assert kit.actions.actions == [MessageTaken(call.call_id, "Call the garage back.")]
    assert said == "The message has been kept for the user."
    assert kit.notes.refusals == ()


async def test_a_message_at_the_limit_is_kept_whole() -> None:
    kit = Kit()
    message = "m" * MAX_MESSAGE_CHARACTERS

    await answered(
        TakeAMessage(kit.notes, kit.actions),
        a_call(authority=MAY_TAKE_A_MESSAGE),
        {"message": message},
    )

    assert kit.actions.of_kind(MessageTaken)[0].message == message


@pytest.mark.parametrize(
    "authority",
    [
        AgentAuthority.none(),
        AgentAuthority.granting(
            *(each for each in Capability if each is not Capability.TAKE_A_MESSAGE)
        ),
    ],
    ids=["nothing granted", "everything but messages"],
)
async def test_without_the_grant_nothing_is_kept(authority: AgentAuthority) -> None:
    kit = Kit()

    reason = await refused(
        TakeAMessage(kit.notes, kit.actions), a_call(authority=authority), {"message": "Hello."}
    )

    assert reason == "the assistant is not authorised to take a message"
    assert kit.actions.actions == []
    assert len(kit.notes.refusals) == 1


@pytest.mark.parametrize(
    ("arguments", "because"),
    [
        ({}, "message is required"),
        ({"message": None}, "message is required"),
        ({"message": 42}, "message must be text"),
        ({"message": {"text": "hello"}}, "message must be text"),
        ({"message": "\t  "}, "message must not be blank"),
        ({"message": "m" * (MAX_MESSAGE_CHARACTERS + 1)}, "message is at most 1000 characters"),
        ({"message": "Hello.", "urgent": True}, "unexpected arguments: urgent"),
    ],
)
async def test_a_malformed_message_is_refused_even_with_the_grant(
    arguments: Mapping[str, object], because: str
) -> None:
    kit = Kit()

    reason = await refused(
        TakeAMessage(kit.notes, kit.actions), a_call(authority=MAY_TAKE_A_MESSAGE), arguments
    )

    assert reason == because
    assert kit.actions.actions == []
