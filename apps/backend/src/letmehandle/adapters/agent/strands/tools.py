"""Application tools, presented to the SDK and nothing more.

A presented tool forwards the model's arguments to `AgentTool.invoke` and hands back what the tool
said. It does not validate, check a grant or decide anything: each tool does that for itself
(D-026), so what the assistant may do does not depend on this file being right.

Two things are kept for the judgement, in a ledger that lives as long as one: the judgement's notes,
which the tools write their refusals and the call's ending to, and which this file writes to only
for arguments that never reached a tool; and a tool that raised. The SDK's own answer to a raising
tool is to tell the model the exception's text and carry on, which both shows a model — and through
it a caller — whatever the exception said, and turns a defect into a sentence nobody reads. Here
the model is told only that the action did not complete, and the agent raises the failure once the
model has finished.

A tool the model asks for that does not exist never reaches this file's wrappers, and the SDK's own
answer is an error the judgement never hears of. `UnknownToolRefusals` records it as a refusal like
any other, so the user sees what their assistant was asked to do even when there was nothing to do
it with.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from strands.hooks import BeforeToolCallEvent, HookProvider, MessageAddedEvent
from strands.tools import InvalidToolUseNameException, PythonAgentTool
from strands.tools.tools import validate_tool_use_name

from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import ToolRefusal

if TYPE_CHECKING:
    from strands.hooks import HookRegistry
    from strands.types.tools import ToolResult as SDKToolResult
    from strands.types.tools import ToolUse

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import AgentTool

# What the model reads when a tool raised. Deliberately says nothing about why.
TOOL_FAILED: Final = "The action could not be completed."

# How a request for a tool nobody has is written down. The name is kept only when the SDK accepts it
# as a tool name at all; anything else is text the model wrote, which a caller may have dictated.
NO_SUCH_TOOL: Final = "there is no tool by that name"
UNKNOWN_TOOL: Final = "unknown_tool"


@dataclass(slots=True)
class ToolLedger:
    """What the tools did during one judgement that the judgement has to report."""

    notes: JudgementNotes = field(default_factory=JudgementNotes)
    failure: Exception | None = None


class UnknownToolRefusals(HookProvider):
    """Refuses, and records, a request for a tool the judgement was not given.

    Two hooks, because the SDK turns such a request away at two points. A name that is not a valid
    tool name is answered before any tool runs, so it is caught as the model's message arrives. A
    valid name nobody registered reaches the executor with no tool selected, and is cancelled there.
    """

    def __init__(self, ledger: ToolLedger) -> None:
        self._ledger = ledger

    def register_hooks(self, registry: HookRegistry, **_kwargs: object) -> None:
        registry.add_callback(MessageAddedEvent, self._refuse_malformed_names)
        registry.add_callback(BeforeToolCallEvent, self._refuse_if_unknown)

    def _refuse_malformed_names(self, event: MessageAddedEvent) -> None:
        if event.message["role"] != "assistant":
            return
        for block in event.message["content"]:
            if "toolUse" not in block:
                continue
            try:
                validate_tool_use_name(block["toolUse"])
            except InvalidToolUseNameException:
                self._ledger.notes.refused(ToolRefusal(UNKNOWN_TOOL, NO_SUCH_TOOL))

    def _refuse_if_unknown(self, event: BeforeToolCallEvent) -> None:
        if event.selected_tool is not None:
            return
        refusal = ToolRefusal(event.tool_use["name"], NO_SUCH_TOOL)
        self._ledger.notes.refused(refusal)
        # Cancelled, so the model reads a refusal like any other rather than the SDK's own error.
        event.cancel_tool = f"Refused: {refusal.reason}"


def present(tool: AgentTool, call: CallSoFar, ledger: ToolLedger) -> PythonAgentTool:
    """`tool`, as the SDK calls tools, acting on `call` and recording into `ledger`."""
    spec = tool.spec

    async def run(tool_use: ToolUse, **_invocation_state: object) -> SDKToolResult:
        arguments = tool_use["input"]
        if not isinstance(arguments, Mapping):
            # The SDK hands on whatever JSON the model wrote. A list or a bare string is a
            # malformed call like any other, refused like one and written down like one.
            refusal = ToolRefusal(spec.name, "the arguments must be a JSON object")
            ledger.notes.refused(refusal)
            return _result(tool_use, f"Refused: {refusal.reason}", succeeded=False)
        try:
            outcome = await tool.invoke(call, arguments)
        except Exception as error:  # noqa: BLE001 - kept, and raised by the agent once the model stops
            if ledger.failure is None:
                ledger.failure = error
            return _result(tool_use, TOOL_FAILED, succeeded=False)

        if isinstance(outcome, ToolRefusal):
            # Already in the notes: the tool that refused wrote it there.
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
