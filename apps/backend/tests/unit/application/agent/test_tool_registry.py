"""The set of tools a judgement is given, and the shape each one shows a model."""

from __future__ import annotations

import json

from letmehandle.application.agent.tools.registry import tools_for_a_judgement
from tests.unit.application.agent.calls import a_call
from tests.unit.application.agent.kit import Kit, answered, refused


def test_a_judgement_is_given_exactly_these_tools() -> None:
    kit = Kit()

    tools = tools_for_a_judgement(kit.actions, kit.escalation, kit.notes)

    assert [tool.spec.name for tool in tools] == [
        "get_user_preferences",
        "get_caller_context",
        "request_human_escalation",
        "take_a_message",
        "record_call_outcome",
        "end_call",
    ]


def test_every_schema_is_a_closed_object_whose_required_fields_it_describes() -> None:
    kit = Kit()

    for tool in tools_for_a_judgement(kit.actions, kit.escalation, kit.notes):
        schema = tool.spec.parameters
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        json.dumps(schema)


async def test_the_tools_share_one_set_of_notes_and_one_escalation_memory() -> None:
    kit = Kit()
    tools = kit.tools()
    call = a_call()

    await refused(tools["take_a_message"], call, {"message": "Hello."})
    await answered(
        tools["request_human_escalation"], call, {"importance": "urgent", "intent": "personal"}
    )
    await answered(tools["end_call"], call, {"ending": "handed_over"})

    assert [refusal.tool for refusal in kit.notes.refusals] == ["take_a_message"]
    assert kit.notes.ended
