"""`end_call`: hang up, and say which kind of ending it was.

Ending a call is three different acts that happen to share a button, and each is held to what it
actually is:

- **Resolved.** The caller got what they came for — a courier knows where to leave the parcel, a
  message was taken. Needs no grant. Ending here only finishes something the assistant was already
  allowed to do, and every one of those things was checked by its own tool when it was done.
- **Handed over.** The user has been reached, or will be, and there is nothing left for the
  assistant to say. Needs no grant, but needs the fact: it is refused unless the escalation service
  has escalated this call. A model that says it handed a call over when it did not is ending a call
  the user was never told about.
- **Declined.** The caller is being turned away on the user's behalf — the sales pitch, the offer,
  the request the assistant says no to. That is `DECLINE_ON_THE_USERS_BEHALF`, and without it the
  assistant does not get to decide that a caller goes unheard. It can still take a message, or ask
  for the user, where those are allowed.

The kind is chosen from a fixed set rather than written as prose, so orchestration and the call
history receive something they can act on, and no caller's words travel with it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

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

    from letmehandle.application.agent.escalation import EscalationService
    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions, CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome


class Ending(StrEnum):
    """Why the assistant is ending the call."""

    RESOLVED = "resolved"
    HANDED_OVER = "handed_over"
    DECLINED = "declined"


ENDING: Final = options_by_value(Ending)

_SPEC: Final = ToolSpec(
    name="end_call",
    description=(
        "End the call. 'resolved' when the caller has what they came for; 'handed_over' only after "
        "the user has been reached for this call; 'declined' to turn the caller away on the user's "
        "behalf, only if you are allowed to decline for them. Say goodbye first."
    ),
    parameters=object_schema(
        {"ending": choice_schema("Which kind of ending this is.", ENDING)},
        "ending",
    ),
)


class EndCall(CheckedTool[Ending]):
    """Ends the call once, for a reason the grant and the call's history both allow."""

    def __init__(
        self, notes: JudgementNotes, actions: CallActions, escalation: EscalationService
    ) -> None:
        super().__init__(notes)
        self._actions = actions
        self._escalation = escalation

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> Ending:
        return required_choice(arguments, "ending", ENDING)

    def _requires(self, parsed: Ending) -> Capability | None:
        return Capability.DECLINE_ON_THE_USERS_BEHALF if parsed is Ending.DECLINED else None

    async def _act(self, call: CallSoFar, parsed: Ending) -> ToolOutcome:
        if self._notes.ended:
            return self.refuse("the call has already been ended")
        if parsed is Ending.HANDED_OVER and not self._escalation.has_escalated(call.call_id):
            return self.refuse(
                "the user has not been reached for this call, so it was not handed over"
            )
        await self._actions.end_call(call.call_id, parsed.value)
        self._notes.call_ended()
        return ToolResult("The call has ended.")
