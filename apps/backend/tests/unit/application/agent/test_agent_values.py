"""The values the agent's ports carry refuse to exist in a state that says nothing."""

from __future__ import annotations

import pytest

from letmehandle.application.agent.ports import OutcomeRecord, ToolRefusal
from letmehandle.application.agent.tool import ToolSpec
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.summary import CallOutcome


@pytest.mark.parametrize(("tool", "reason"), [("", "because"), ("end_call", "  ")])
def test_a_refusal_names_the_tool_and_says_why(tool: str, reason: str) -> None:
    # A refusal is shown to the user; one that names nothing or explains nothing is noise.
    with pytest.raises(InvariantError):
        ToolRefusal(tool=tool, reason=reason)


def test_an_outcome_needs_a_headline() -> None:
    with pytest.raises(InvariantError):
        OutcomeRecord(outcome=CallOutcome.RESOLVED_BY_AGENT, headline=" ")


def test_a_tool_name_is_an_identifier_a_model_can_be_shown() -> None:
    with pytest.raises(InvariantError, match="identifier"):
        ToolSpec(name="end call", description="Ends the call.", parameters={})


def test_a_tool_says_what_it_is_for() -> None:
    with pytest.raises(InvariantError, match="description"):
        ToolSpec(name="end_call", description="", parameters={})
