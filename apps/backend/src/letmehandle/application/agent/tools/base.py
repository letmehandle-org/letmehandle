"""The order every tool runs in, written once.

Read the arguments, check the grant, then act. A tool that acts before it checks has already done
the thing it was not allowed to do by the time it finds out, and a check that each tool writes for
itself is a check one of them gets in the wrong order. So the order is here, and a tool supplies
only the three steps.

Every refusal passes through `refuse`, which is what records it. A tool cannot produce a refusal
the user does not get to see.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from letmehandle.application.agent.ports import ToolRefusal
from letmehandle.application.agent.tool import AgentTool
from letmehandle.application.agent.tools.arguments import MalformedArgumentsError, expect_only
from letmehandle.domain.errors import NotAuthorisedError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome
    from letmehandle.domain.models.authority import Capability


class CheckedTool[Parsed](AgentTool):
    """A tool that parses, checks, then acts, and records every refusal it gives."""

    def __init__(self, notes: JudgementNotes) -> None:
        self._notes = notes

    async def invoke(self, call: CallSoFar, arguments: Mapping[str, object]) -> ToolOutcome:
        try:
            expect_only(arguments, self.spec.parameters["properties"])
            parsed = self._parse(arguments)
            required = self._requires(parsed)
            # The grant is read from the call's authority and nothing else. Not the transcript,
            # not the arguments, not anything the model was told: a caller can put words in all
            # of those, and the user's settings are the one input they cannot reach.
            if required is not None:
                call.authority.require(required)
        except (MalformedArgumentsError, NotAuthorisedError) as error:
            return self.refuse(str(error))
        return await self._act(call, parsed)

    def refuse(self, reason: str) -> ToolRefusal:
        """Decline, and write it down."""
        refusal = ToolRefusal(tool=self.spec.name, reason=reason)
        self._notes.refused(refusal)
        return refusal

    @abstractmethod
    def _parse(self, arguments: Mapping[str, object]) -> Parsed:
        """Turn the model's arguments into domain values, or raise `MalformedArgumentsError`."""

    def _requires(self, parsed: Parsed) -> Capability | None:
        """The grant this particular use needs, if any."""
        return None

    @abstractmethod
    async def _act(self, call: CallSoFar, parsed: Parsed) -> ToolOutcome:
        """Do the thing. Reached only with valid arguments and the grant in hand."""
