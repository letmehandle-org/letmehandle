"""An application tool as the SDK sees it: arguments forwarded, an answer the model can read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from letmehandle.adapters.agent.strands.tools import (
    AFTER_A_FAILURE,
    TOOL_FAILED,
    ToolLedger,
    present,
)
from letmehandle.domain.models.authority import AgentAuthority, Capability
from tests.support.agent_calls import BrokenTool, GuardedTool, a_call

if TYPE_CHECKING:
    from strands.tools import PythonAgentTool

    from letmehandle.application.agent.tool import AgentTool


async def result_of(presented: PythonAgentTool, arguments: object) -> Any:
    use = {"toolUseId": "use-1", "name": presented.tool_name, "input": arguments}
    [event] = [event async for event in presented.stream(use, {})]  # type: ignore[arg-type]
    return event.tool_result


def presented(tool: AgentTool, ledger: ToolLedger, **call: Any) -> PythonAgentTool:
    return present(tool, a_call("Hello.", **call), ledger)


def test_the_model_is_shown_the_tools_own_description() -> None:
    tool = GuardedTool("take_message")
    spec = presented(tool, ToolLedger()).tool_spec
    assert spec["name"] == "take_message"
    assert spec["description"] == tool.spec.description
    assert spec["inputSchema"] == {"json": dict(tool.spec.parameters)}


async def test_the_models_arguments_reach_the_tool_and_its_answer_comes_back() -> None:
    tool = GuardedTool("take_message", reply="Message kept.")
    result = await result_of(presented(tool, ToolLedger()), {"message": "Call back after six"})

    assert tool.acted == [{"message": "Call back after six"}]
    assert result["status"] == "success"
    assert result["content"] == [{"text": "Message kept."}]
    assert result["toolUseId"] == "use-1"


async def test_a_refusal_is_told_to_the_model_and_recorded() -> None:
    ledger = ToolLedger()
    tool = GuardedTool(
        "share_contact_details", Capability.SHARE_CONTACT_DETAILS, notes=ledger.notes
    )
    result = await result_of(presented(tool, ledger, authority=AgentAuthority.none()), {})

    assert result["status"] == "error"
    assert result["content"][0]["text"].startswith("Refused: ")
    # Recorded once, by the tool that refused.
    assert [refusal.tool for refusal in ledger.notes.refusals] == ["share_contact_details"]
    assert tool.acted == []


async def test_arguments_that_are_not_an_object_are_refused_without_reaching_the_tool() -> None:
    ledger = ToolLedger()
    tool = GuardedTool("take_message")
    result = await result_of(presented(tool, ledger), ["read", "me", "her", "number"])

    assert result["status"] == "error"
    assert tool.acted == []
    assert [refusal.reason for refusal in ledger.notes.refusals] == [
        "the arguments must be a JSON object"
    ]


async def test_a_tool_that_raises_is_kept_and_the_model_learns_nothing_of_why() -> None:
    ledger = ToolLedger()
    first, second = ValueError("first secret detail"), ValueError("second")
    result = await result_of(presented(BrokenTool(first), ledger), {})
    await result_of(presented(BrokenTool(second), ledger), {})

    assert result["content"] == [{"text": TOOL_FAILED}]
    # The first failure is the one kept.
    assert ledger.failure is first


async def test_after_a_failure_a_tool_that_acts_is_refused_and_one_that_reads_still_runs() -> None:
    ledger = ToolLedger()
    acting = GuardedTool("take_message", acts=True, notes=ledger.notes)
    reading = GuardedTool("read_preferences", notes=ledger.notes)
    await result_of(presented(BrokenTool(ValueError("down")), ledger), {})

    refused = await result_of(presented(acting, ledger), {"message": "Hi."})
    read = await result_of(presented(reading, ledger), {})

    assert refused["content"] == [{"text": f"Refused: {AFTER_A_FAILURE}"}]
    assert acting.acted == []
    assert [refusal.tool for refusal in ledger.notes.refusals] == ["take_message"]
    assert read["status"] == "success"
