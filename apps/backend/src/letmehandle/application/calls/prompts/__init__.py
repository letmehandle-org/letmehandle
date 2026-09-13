"""What the summarising model is told, from versioned template files, with the call as data."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.prompts.templates import (
    TemplateVersion,
    as_data,
    transcript_as_data,
)
from letmehandle.application.calls.summary_checks import vocabulary_for
from letmehandle.application.preferences.context import normalise_locale
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable
    from string import Template

    from letmehandle.application.calls.summary_draft import DraftCorrection, SummaryRequest

SUMMARY_PROMPT_VERSION: Final = "v4"

_PLACEHOLDERS: Final = {
    "instructions.md": frozenset({"answer_tool"}),
    "call.md": frozenset({"call", "transcript"}),
    "answer.md": frozenset({"answer_tool"}),
    "correction.md": frozenset({"answer_tool", "draft", "problems"}),
}


@dataclass(frozen=True, slots=True)
class SummaryPrompts:
    """One version of the summary templates, in one language."""

    version: str
    language: str
    instructions: Template
    call: Template
    answer: Template
    correction: Template | None

    def instructions_prompt(self, *, answer_tool: str) -> str:
        """The instructions, which carry nothing from any call."""
        return self.instructions.substitute(answer_tool=answer_tool)

    def call_message(self, request: SummaryRequest) -> str:
        """The call's known facts and transcript as data, with a name only for a known caller."""
        known = request.known
        caller = known.caller
        call = {
            "how_it_ended": known.outcome.value,
            "name_the_ending_with_one_of": list(
                vocabulary_for(request.locale).endings[known.outcome]
            ),
            "caller_category": caller.category.value,
            "caller_name": caller.display_name if caller.is_known else None,
            "user_joined": known.human_joined,
            # Written in the user's language, not the caller's.
            "write_for_locale": normalise_locale(request.locale),
        }
        return self.call.substitute(
            call=as_data(call), transcript=transcript_as_data(request.transcript)
        )

    def answer_request(self, *, answer_tool: str) -> str:
        """What the model is told when it stops without writing the summary."""
        return self.answer.substitute(answer_tool=answer_tool)

    def correction_message(self, correction: DraftCorrection, *, answer_tool: str) -> str:
        """The refused draft and its problems as data, for the model to correct."""
        if self.correction is None:
            raise InvariantError(
                f"version {self.version!r} of the summary prompts cannot ask for a correction"
            )
        refused = correction.refused
        draft = {
            "headline": refused.headline,
            "intent": refused.intent.value,
            "outcome": refused.outcome.value,
            "details": [
                {"kind": each.kind.value, "value": each.value, "evidence": each.evidence}
                for each in refused.details
            ],
        }
        return self.correction.substitute(
            answer_tool=answer_tool,
            draft=as_data(draft),
            problems=as_data([problem.value for problem in correction.problems]),
        )


@lru_cache(maxsize=16)
def load_summary_prompts(locale: str, version: str = SUMMARY_PROMPT_VERSION) -> SummaryPrompts:
    """The templates of `version` closest to `locale`, narrowing to its language, then English."""
    return read_summary_prompts(files(__name__), locale, version)


def read_summary_prompts(templates: Traversable, locale: str, version: str) -> SummaryPrompts:
    """`load_summary_prompts`, from a directory of versions other than the one shipped."""
    found = TemplateVersion.open(
        templates,
        kind="summary prompts",
        locale=locale,
        version=version,
        placeholders=_PLACEHOLDERS,
    )
    language, instructions = found.required("instructions.md")
    return SummaryPrompts(
        version=version,
        language=language,
        instructions=instructions,
        call=found.required("call.md")[1],
        answer=found.required("answer.md")[1],
        correction=found.optional("correction.md"),
    )
