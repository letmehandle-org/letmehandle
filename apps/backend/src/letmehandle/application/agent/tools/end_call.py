"""`end_call`: ask for the call to end, and say which kind of ending it is.

The tool asks; it does not hang up. A model can ask to end a call before it has finished working
out what the call was, and the rest of its judgement — an escalation the user's rules require —
must still be able to overrule that. So the ending is written down, and the conclusion applies it
once the model has finished, after any escalation, if the rules still allow it then.

Ending a call is three different acts that happen to share a button, and each is held to what it
actually is:

- **Resolved.** The caller got what they came for — a courier knows where to leave the parcel, a
  message was taken. Needs no grant, but is not applied to a call the user's rules say needs them:
  a hang-up never cancels an escalation.
- **Handed over.** The user has been reached and there is nothing left for the assistant to say.
  Needs no grant, but needs the fact: it is applied only when this judgement reached the user
  immediately. A model that says it handed a call over when it did not is ending a call the user
  was never told about.
- **Declined.** The caller is being turned away on the user's behalf — the sales pitch, the offer,
  the request the assistant says no to. That is `DECLINE_ON_THE_USERS_BEHALF`, checked here, and
  without it the assistant does not get to decide that a caller goes unheard. Like a resolved call,
  it is not applied to a call the user's rules say needs them.

The kind is a `CallEnding`, chosen from a fixed set rather than written as prose.
"""

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
