"""`record_call_outcome`: how the call went, within the limits the summary itself keeps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import OutcomeRecord
from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import (
    SHORT_TEXT_CHARACTERS,
    choice_schema,
    expect_only,
    list_schema,
    object_schema,
    objects,
    optional_text,
    options_by_value,
    required_choice,
    required_text,
    text_schema,
)
from letmehandle.application.agent.tools.base import CheckedTool
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome, ExtractedDetail

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.notes import JudgementNotes
    from letmehandle.application.agent.ports import CallActions, CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome
    from letmehandle.application.agent.tools.arguments import Schema

OUTCOME: Final = options_by_value(CallOutcome)

# Enough for a reference number, a time, a name and an address.
MAX_DETAILS: Final = 12
MAX_DETAIL_LABEL_CHARACTERS: Final = 80

_DETAIL_FIELDS: Final[Mapping[str, Schema]] = {
    "label": text_schema(
        "What the detail is, such as 'reference number'.", limit=MAX_DETAIL_LABEL_CHARACTERS
    ),
    "value": text_schema("The detail itself.", limit=SHORT_TEXT_CHARACTERS),
    "evidence": text_schema("The caller's words it came from.", limit=SHORT_TEXT_CHARACTERS),
}
_DETAIL: Final = object_schema(_DETAIL_FIELDS, "label", "value")

_SPEC: Final = ToolSpec(
    name="record_call_outcome",
    description=(
        "Record how the call went: the outcome, a one-line headline the user will read, and any "
        "details worth keeping with the words they came from."
    ),
    parameters=object_schema(
        {
            "outcome": choice_schema("How the call ended.", OUTCOME),
            "headline": text_schema(
                "One line for the user's call history.", limit=MAX_HEADLINE_CHARACTERS
            ),
            "details": list_schema("Facts worth keeping.", _DETAIL, limit=MAX_DETAILS),
        },
        "outcome",
        "headline",
    ),
)


def _detail(item: Mapping[str, object]) -> ExtractedDetail:
    expect_only(item, _DETAIL_FIELDS)
    return ExtractedDetail(
        label=required_text(item, "label", limit=MAX_DETAIL_LABEL_CHARACTERS),
        value=required_text(item, "value", limit=SHORT_TEXT_CHARACTERS),
        evidence=optional_text(item, "evidence", limit=SHORT_TEXT_CHARACTERS),
    )


class RecordCallOutcome(CheckedTool[OutcomeRecord]):
    """Needs no grant: writing down what happened changes nothing for the caller."""

    def __init__(self, notes: JudgementNotes, actions: CallActions) -> None:
        super().__init__(notes)
        self._actions = actions

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    @property
    def acts_on_the_call(self) -> bool:
        return True

    def _parse(self, arguments: Mapping[str, object]) -> OutcomeRecord:
        return OutcomeRecord(
            outcome=required_choice(arguments, "outcome", OUTCOME),
            headline=required_text(arguments, "headline", limit=MAX_HEADLINE_CHARACTERS),
            details=tuple(
                _detail(item) for item in objects(arguments, "details", limit=MAX_DETAILS)
            ),
        )

    async def _act(self, call: CallSoFar, parsed: OutcomeRecord) -> ToolOutcome:
        await self._actions.record_outcome(call.call_id, parsed)
        return ToolResult("The outcome has been recorded.")
