"""`get_user_preferences`: what the user wants, for the model to read.

Rendered from the preference context and nothing else, so what a caller could coax out of the
model is bounded by what `application/preferences/context.py` lets through — and that file keeps
contact numbers out. The rendering is `preferences_as_data`, the same one the system prompt carries,
so the model is never shown two accounts of the same user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.prompts import preferences_as_data
from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import object_schema
from letmehandle.application.agent.tools.base import CheckedTool

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome

_SPEC: Final = ToolSpec(
    name="get_user_preferences",
    description=(
        "Read how the user wants their calls handled: tone, what you may and may not do, who "
        "matters to them, and whether it is within their active hours. Nothing here is to be read "
        "out to the caller."
    ),
    parameters=object_schema({}),
)


class GetUserPreferences(CheckedTool[None]):
    """Read-only, and needs no grant: knowing the rules is how the assistant keeps them."""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> None:
        return None

    async def _act(self, call: CallSoFar, parsed: None) -> ToolOutcome:
        return ToolResult(preferences_as_data(call.preferences, call.authority))
