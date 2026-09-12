"""Application tools, presented to the SDK and nothing more.

A presented tool forwards the model's arguments to `AgentTool.invoke` and hands back what the tool
said. It does not validate, check a grant or decide anything: each tool does that for itself
(D-026), so what the assistant may do does not depend on this file being right.

Two things are kept for the judgement, in a ledger that lives as long as one: the refusals, in the
order they happened, so the user can see what was asked of their assistant; and a tool that
raised. The SDK's own answer to a raising tool is to tell the model the exception's text and carry
on, which both shows a model — and through it a caller — whatever the exception said, and turns a
defect into a sentence nobody reads. Here the model is told only that the action did not complete,
and the agent raises the failure once the model has finished.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from strands.tools import PythonAgentTool

from letmehandle.application.agent.ports import ToolRefusal

if TYPE_CHECKING:
    from strands.types.tools import ToolResult as SDKToolResult
    from strands.types.tools import ToolUse

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import AgentTool

# What the model reads when a tool raised. Deliberately says nothing about why.
TOOL_FAILED: Final = "The action could not be completed."


@dataclass(slots=True)
class ToolLedger:
    """What the tools did during one judgement that the judgement has to report."""

    refusals: list[ToolRefusal] = field(default_factory=list)
    failure: Exception | None = None


def present(tool: AgentTool, call: CallSoFar, ledger: ToolLedger) -> PythonAgentTool:
    """`tool`, as the SDK calls tools, acting on `call` and recording into `ledger`."""
    spec = tool.spec

    async def run(tool_use: ToolUse, **_invocation_state: object) -> SDKToolResult:
        arguments = tool_use["input"]
        try:
            outcome = (
                await tool.invoke(call, arguments)
                if isinstance(arguments, Mapping)
                # The SDK hands on whatever JSON the model wrote. A list or a bare string is a
                # malformed call like any other, and refused like one.
                else ToolRefusal(spec.name, "the arguments must be a JSON object")
            )
        except Exception as error:  # noqa: BLE001 - kept, and raised by the agent once the model stops
            if ledger.failure is None:
                ledger.failure = error
            return _result(tool_use, TOOL_FAILED, succeeded=False)

        if isinstance(outcome, ToolRefusal):
            ledger.refusals.append(outcome)
            return _result(tool_use, f"Refused: {outcome.reason}", succeeded=False)
        return _result(tool_use, outcome.content, succeeded=True)

    return PythonAgentTool(
        spec.name,
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": {"json": dict(spec.parameters)},
        },
        run,
    )


def _result(tool_use: ToolUse, text: str, *, succeeded: bool) -> SDKToolResult:
    return {
        "toolUseId": tool_use["toolUseId"],
        "status": "success" if succeeded else "error",
        "content": [{"text": text}],
    }
