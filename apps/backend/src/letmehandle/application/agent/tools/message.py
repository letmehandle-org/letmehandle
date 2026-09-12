"""`take_a_message`: keep what the caller wants the user to hear.

Needs `TAKE_A_MESSAGE`. A message is a promise made to the caller that the user will read it, and a
user who has not agreed to receive messages from strangers has not agreed to that promise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import (
    object_schema,
    required_text,
    text_schema,
)
from letmehandle.application.agent.tools.base import CheckedTool
from letmehandle.domain.models.authority import Capability

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions, CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome

# A message, not a monologue. Long enough for a name, a reason and a way back; short enough that
# nobody fills the user's history with a speech they were paid to leave.
MAX_MESSAGE_CHARACTERS: Final = 1000

_SPEC: Final = ToolSpec(
    name="take_a_message",
    description=(
        "Keep a message the caller wants the user to have, in the caller's words. Only if you are "
        "allowed to take messages."
    ),
    parameters=object_schema(
        {
            "message": text_schema(
                "The message, as the caller gave it.", limit=MAX_MESSAGE_CHARACTERS
            )
        },
        "message",
    ),
)


class TakeAMessage(CheckedTool[str]):
    """Keeps one message for the user, when the user allows messages."""

    def __init__(self, notes: JudgementNotes, actions: CallActions) -> None:
        super().__init__(notes)
        self._actions = actions

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    @property
    def acts_on_the_call(self) -> bool:
        return True

    def _parse(self, arguments: Mapping[str, object]) -> str:
        return required_text(arguments, "message", limit=MAX_MESSAGE_CHARACTERS)

    def _requires(self, parsed: str) -> Capability:
        return Capability.TAKE_A_MESSAGE

    async def _act(self, call: CallSoFar, parsed: str) -> ToolOutcome:
        await self._actions.take_message(call.call_id, parsed)
        return ToolResult("The message has been kept for the user.")
