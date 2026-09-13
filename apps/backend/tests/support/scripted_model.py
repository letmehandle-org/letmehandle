"""A model for the Strands SDK that answers from a script.

It implements the SDK's own `Model` and speaks through `stream` in the event shape a real provider
produces — a message start, content blocks, a stop reason — so the SDK's agent loop runs for real
around it: tools are executed, results are fed back, a missing assessment is asked for again, and a
turn limit is counted. Only the words are fixed in advance.

Each request the SDK makes is kept, so a test can check what the model was actually shown: which
system prompt, which messages, which tools.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from strands.models.model import Model

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterable, Sequence

    from pydantic import BaseModel
    from strands.types.content import ContentBlockStartToolUse, Messages
    from strands.types.event_loop import StopReason
    from strands.types.streaming import StreamEvent
    from strands.types.tools import ToolSpec


@dataclass(frozen=True, slots=True)
class CallTool:
    """Ask for a tool, with arguments as JSON — or, with `raw`, with text that may not be JSON."""

    name: str
    arguments: object = field(default_factory=dict)
    raw: str | None = None


@dataclass(frozen=True, slots=True)
class Say:
    """Say something and end the turn, without calling a tool."""

    text: str


@dataclass(frozen=True, slots=True)
class CutOff:
    """Stop mid-answer, the way a model does when it runs out of tokens."""

    text: str


@dataclass(frozen=True, slots=True)
class Fail:
    """Raise, the way a provider does when the endpoint refuses or is unreachable."""

    error: Exception


@dataclass(frozen=True, slots=True)
class Hang:
    """Never answer."""


type Step = CallTool | Say | CutOff | Fail | Hang


def assess(**fields: object) -> CallTool:
    """Record an assessment, filling in whatever a test does not care about."""
    assessment: dict[str, object] = {
        "intent": "enquiry",
        "importance": "routine",
        "understood": True,
        "caller_asked_for_the_user": False,
        "needs_the_users_decision": False,
        "requested_capability": None,
        "caller_summary": None,
    }
    assessment.update(fields)
    return CallTool("CallAssessment", assessment)


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """One thing the SDK asked the model."""

    system_prompt: str | None
    messages: Messages
    tool_names: tuple[str, ...]


class ScriptedModel(Model):
    """Replays its steps, one per request. Out of steps, it ends the turn saying nothing."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[ModelRequest] = []

    @property
    def unused_steps(self) -> int:
        return len(self._steps)

    def update_config(self, **model_config: Any) -> None:
        raise NotImplementedError("a scripted model has nothing to configure")

    def get_config(self) -> dict[str, Any]:
        return {}

    async def structured_output(
        self,
        output_model: type[BaseModel],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        # The SDK's agent asks for structured output through a tool in `stream`; this method is
        # only reached by its deprecated path, which nothing here uses.
        raise NotImplementedError("structured output arrives through a tool call")
        yield {}

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.requests.append(
            ModelRequest(
                system_prompt=system_prompt,
                messages=json.loads(json.dumps(messages)),
                tool_names=tuple(spec["name"] for spec in tool_specs or ()),
            )
        )
        step = self._steps.pop(0) if self._steps else Say("")
        yield {"messageStart": {"role": "assistant"}}
        match step:
            case CallTool():
                use: ContentBlockStartToolUse = {
                    "name": step.name,
                    "toolUseId": f"tool-use-{len(self.requests)}",
                }
                yield {"contentBlockStart": {"start": {"toolUse": use}}}
                text = json.dumps(step.arguments) if step.raw is None else step.raw
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": text}}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            case Say(text=text) | CutOff(text=text):
                yield {"contentBlockDelta": {"delta": {"text": text}}}
                yield {"contentBlockStop": {}}
                reason: StopReason = "max_tokens" if isinstance(step, CutOff) else "end_turn"
                yield {"messageStop": {"stopReason": reason}}
            case Fail(error=error):
                raise error
            case Hang():
                await asyncio.Event().wait()
