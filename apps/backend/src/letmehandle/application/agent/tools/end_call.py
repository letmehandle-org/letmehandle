"""`end_call`: asks for a resolved, handed-over or declined ending, applied by the conclusion."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import CallEnding
from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import (
    choice_schema,
    object_schema,
    options_by_value,
    required_choice,
)
from letmehandle.application.agent.tools.base import CheckedTool
from letmehandle.domain.models.authority import Capability

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome


END_CALL: Final = "end_call"
ENDING: Final = options_by_value(CallEnding)

_SPEC: Final = ToolSpec(
    name=END_CALL,
    description=(
        "Ask for the call to end. 'resolved' when the caller has what they came for; 'handed_over' "
        "only when the user is being reached now; 'declined' to turn the caller away on the user's "
        "behalf, only if you are allowed to decline for them. The call ends after your assessment, "
        "and only if the user's rules still allow it then."
    ),
    parameters=object_schema(
        {"ending": choice_schema("Which kind of ending this is.", ENDING)},
        "ending",
    ),
)


class EndCall(CheckedTool[CallEnding]):
    """Writes down the one ending the model asked for, when the grant allows that kind."""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> CallEnding:
        return required_choice(arguments, "ending", ENDING)

    def _requires(self, parsed: CallEnding) -> Capability | None:
        return Capability.DECLINE_ON_THE_USERS_BEHALF if parsed is CallEnding.DECLINED else None

    async def _act(self, call: CallSoFar, parsed: CallEnding) -> ToolOutcome:
        if self._notes.requested_ending is not None:
            return self.refuse("an ending has already been asked for")
        self._notes.ending_requested(parsed)
        return ToolResult(
            "The call will end once your assessment is recorded, if the user's rules still allow "
            "it then."
        )
