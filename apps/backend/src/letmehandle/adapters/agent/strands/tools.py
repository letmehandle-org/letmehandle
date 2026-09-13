"""Application tools presented to the SDK, with a raising tool kept and hidden from the model."""

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

# What the model reads when a tool raised; it says nothing about why.
TOOL_FAILED: Final = "The action could not be completed."

# Why a tool that acts on the call is refused once another has failed.
AFTER_A_FAILURE: Final = "an earlier action on this call failed, so nothing more is done"

# How a request for an absent tool is recorded; a name the SDK refuses is never kept.
NO_SUCH_TOOL: Final = "there is no tool by that name"
UNKNOWN_TOOL: Final = "unknown_tool"


@dataclass(slots=True)
class ToolLedger:
    """What the tools did during one judgement that the judgement has to report."""

    notes: JudgementNotes = field(default_factory=JudgementNotes)
    failure: Exception | None = None


class UnknownToolRefusals(HookProvider):
    """Refuses and records a request for a tool the judgement was not given."""

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
        # Cancelled, so the model reads a refusal like any other.
        event.cancel_tool = _refusal_text(refusal)


def present(tool: AgentTool, call: CallSoFar, ledger: ToolLedger) -> PythonAgentTool:
    """`tool`, as the SDK calls tools, acting on `call` and recording into `ledger`."""
    spec = tool.spec

    async def run(tool_use: ToolUse, **_invocation_state: object) -> SDKToolResult:
        arguments = tool_use["input"]
        if not isinstance(arguments, Mapping):
            return _refused(tool_use, ledger, spec.name, "the arguments must be a JSON object")
        if ledger.failure is not None and tool.acts_on_the_call:
            return _refused(tool_use, ledger, spec.name, AFTER_A_FAILURE)
        try:
            outcome = await tool.invoke(call, arguments)
        except Exception as error:  # noqa: BLE001 - kept, and raised by the agent once the model stops
            if ledger.failure is None:
                ledger.failure = error
            return _result(tool_use, TOOL_FAILED, succeeded=False)

        if isinstance(outcome, ToolRefusal):
            # Already recorded by the tool that refused.
            return _result(tool_use, _refusal_text(outcome), succeeded=False)
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


def _refused(tool_use: ToolUse, ledger: ToolLedger, tool: str, reason: str) -> SDKToolResult:
    """Record a refusal the wrapper gives, and answer the model with it."""
    refusal = ToolRefusal(tool, reason)
    ledger.notes.refused(refusal)
    return _result(tool_use, _refusal_text(refusal), succeeded=False)


def _refusal_text(refusal: ToolRefusal) -> str:
    return f"Refused: {refusal.reason}"
