"""The shape every tool has, whatever framework presents it to a model.

A tool is described by a name, a sentence and a JSON schema — which is what any model that calls
tools is shown — and invoked with the arguments the model produced. Those arguments are untrusted
text shaped like JSON: a tool parses them into domain types itself, and a malformed or unauthorised
call is a `ToolRefusal`, not an exception, so the model can recover and the refusal is recorded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallSoFar, ToolRefusal


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """How a tool is presented to a model."""

    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise InvariantError("a tool name is an identifier; models are shown it verbatim")
        if not self.description.strip():
            raise InvariantError("a tool with no description is a tool a model guesses about")


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool did, in words the model can read back."""

    content: str


type ToolOutcome = ToolResult | ToolRefusal


class AgentTool(ABC):
    """One thing the agent can do. The only way it affects anything."""

    @property
    @abstractmethod
    def spec(self) -> ToolSpec:
        """Its name, what it is for, and the arguments it takes."""

    @abstractmethod
    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        """Validate, check the user's grant, then act — in that order, every time.

        A refusal is written to the judgement's notes by the tool that gives it, as well as
        returned. The notes are the one record of what was refused; nothing else keeps a copy.
        """


# The tools for one judgement, given the notes that judgement's tools all write to. A function
# rather than a list, because the notes are new for every judgement and the tools hold them.
type ToolsForAJudgement = Callable[[JudgementNotes], Sequence[AgentTool]]
