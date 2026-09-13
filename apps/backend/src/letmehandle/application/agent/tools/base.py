"""The order every tool runs in: parse the arguments, check the grant, then act."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING

from letmehandle.application.agent.ports import ToolRefusal
from letmehandle.application.agent.tool import AgentTool
from letmehandle.application.agent.tools.arguments import MalformedArgumentsError, expect_only
from letmehandle.application.preferences.context import phrasebook_for

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
        except MalformedArgumentsError as error:
            return self.refuse(str(error))
        required = self._requires(parsed)
        # The grant comes from the call's authority alone, which no caller can reach.
        if required is not None and not call.authority.allows(required):
            # Worded in the user's language, because the user reads it in their call history.
            action = phrasebook_for(call.preferences.locale).capability[required]
            return self.refuse(f"the assistant is not authorised to {action}")
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
