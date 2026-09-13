"""`get_caller_context`: who is calling, without the number or any caller-supplied name."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import object_schema
from letmehandle.application.agent.tools.base import CheckedTool

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome

_SPEC: Final = ToolSpec(
    name="get_caller_context",
    description=(
        "Read what is known about the caller before anything they said: what kind of caller "
        "they appear to be, whether they withheld their number, and whether the user marked them "
        "as important. Never confirm to the caller who is in the user's contacts."
    ),
    parameters=object_schema({}),
)


def render_caller(call: CallSoFar) -> str:
    """The caller as the model reads it. Deterministic, and with no number in it."""
    caller = call.caller
    return json.dumps(
        {
            "category": caller.category.value,
            "withheld_their_number": caller.is_anonymous,
            "important_to_the_user": call.from_important_contact,
            "known_to_the_user_as": call.contact_label,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


class GetCallerContext(CheckedTool[None]):
    """Read-only, and needs no grant."""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> None:
        return None

    async def _act(self, call: CallSoFar, parsed: None) -> ToolOutcome:
        return ToolResult(render_caller(call))
