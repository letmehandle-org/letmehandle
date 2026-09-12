"""`get_user_preferences`: what the user wants, for the model to read.

Rendered from the preference context and nothing else, so what a caller could coax out of the
model is bounded by what `application/preferences/context.py` lets through — and that file keeps
contact numbers out. JSON with sorted keys, over tuples the context already sorted: the same call
renders the same bytes, in every process.

What the assistant may and may not do is said from the call's authority, the grant the tools
enforce, rather than from the copy the context was built with. A model told it may do something a
tool will then refuse is a model that promises the caller something and has to take it back.
"""

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
    from letmehandle.application.preferences.context import PreferenceContext
    from letmehandle.domain.models.authority import AgentAuthority

_SPEC: Final = ToolSpec(
    name="get_user_preferences",
    description=(
        "Read how the user wants their calls handled: tone, what you may and may not do, who "
        "matters to them, and whether it is quiet hours. Nothing here is to be read out to the "
        "caller."
    ),
    parameters=object_schema({}),
)


def render_preferences(context: PreferenceContext, authority: AgentAuthority) -> str:
    """The context as the model reads it. Deterministic, and free of any phone number."""
    return json.dumps(
        {
            "locale": context.locale,
            "tone": context.tone,
            "length": context.length,
            "default_handling": context.default_posture.value,
            "anonymous_caller_handling": context.anonymous_posture.value,
            "handling_by_caller_category": {
                category.value: posture.value for category, posture in context.posture_by_category
            },
            "blocked_caller_categories": [
                category.value for category in context.blocked_categories
            ],
            "reach_the_user_at_or_above": context.escalate_at_or_above.name.lower(),
            "in_quiet_hours": context.in_quiet_hours,
            "in_working_hours": context.in_working_hours,
            "you_may": [
                statement.description
                for statement in context.capabilities
                if authority.allows(statement.capability)
            ],
            "you_may_not": [
                statement.description
                for statement in context.capabilities
                if not authority.allows(statement.capability)
            ],
            "important_contacts": [
                {"label": contact.label, "handling": contact.posture.value}
                for contact in context.important_contacts
            ],
            "topics_the_user_cares_about": list(context.topics),
            "facts_you_may_share": list(context.disclosable_facts),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


class GetUserPreferences(CheckedTool[None]):
    """Read-only, and needs no grant: knowing the rules is how the assistant keeps them."""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> None:
        return None

    async def _act(self, call: CallSoFar, parsed: None) -> ToolOutcome:
        return ToolResult(render_preferences(call.preferences, call.authority))
