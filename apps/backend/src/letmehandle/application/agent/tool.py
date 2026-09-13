"""The shape every tool has, whatever framework presents it to a model."""

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

    @property
    def acts_on_the_call(self) -> bool:
        """Whether using it changes something for the caller or the user."""
        return False

    @abstractmethod
    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        """Validates, checks the grant, then acts; a refusal goes to the judgement's notes."""


# The tools for one judgement, built around that judgement's notes.
type ToolsForAJudgement = Callable[[JudgementNotes], Sequence[AgentTool]]
