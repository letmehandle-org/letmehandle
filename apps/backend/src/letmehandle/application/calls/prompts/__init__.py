"""What the model writing a call's summary is told, read from versioned template files.

Laid out as the agent's prompts are (`v1/en/instructions.md`), for the same reasons: a change to
what the model is told is prose somebody can review and a change the summary evaluation measures.
Nothing about a particular call or user is written in a template. What orchestration knows about
the call, and what was said on it, arrive as data in a message of their own, rendered by the agent
prompts' `as_data` so no caller can speak a closing delimiter.

The phrases a headline may name its ending with are rendered from the same vocabulary the checks
read, so the model is asked for exactly what will be accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from string import Template
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.prompts import as_data
from letmehandle.application.calls.summary_checks import vocabulary_for
from letmehandle.application.preferences.context import DEFAULT_LOCALE, normalise_locale
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from importlib.resources.abc import Traversable

    from letmehandle.application.calls.summariser import SummaryRequest

SUMMARY_PROMPT_VERSION: Final = "v1"

_PLACEHOLDERS: Final = {
    "instructions.md": frozenset({"answer_tool"}),
    "call.md": frozenset({"call", "transcript"}),
    "answer.md": frozenset({"answer_tool"}),
}


@dataclass(frozen=True, slots=True)
class SummaryPrompts:
    """One version of the summary templates, in one language."""

    version: str
    language: str
    instructions: Template
    call: Template
    answer: Template

    def instructions_prompt(self, *, answer_tool: str) -> str:
        """The instructions, which carry nothing from any call."""
        return self.instructions.substitute(answer_tool=answer_tool)

    def call_message(self, request: SummaryRequest) -> str:
        """What is known about the call, and what was said on it, delimited as data.

        The caller is described by category, and by name only when the user saved them as a
        contact: the fallback's own rule, so a model is never handed a stranger's claimed name to
        repeat. No phone number is sent.
        """
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
        }
        spoken = [
            {"speaker": entry.speaker.value, "text": entry.text} for entry in request.transcript
        ]
        return self.call.substitute(call=as_data(call), transcript=as_data(spoken))

    def answer_request(self, *, answer_tool: str) -> str:
        """What the model is told when it stops without writing the summary."""
        return self.answer.substitute(answer_tool=answer_tool)


@lru_cache(maxsize=16)
def load_summary_prompts(locale: str, version: str = SUMMARY_PROMPT_VERSION) -> SummaryPrompts:
    """The templates of `version` closest to `locale`, narrowing to its language, then English."""
    return read_summary_prompts(files(__name__), locale, version)


def read_summary_prompts(templates: Traversable, locale: str, version: str) -> SummaryPrompts:
    """`load_summary_prompts`, from a directory of versions other than the one shipped."""
    root = templates.joinpath(version)
    if not root.is_dir():
        raise InvariantError(f"there are no summary prompts of version {version!r}")
    normalised = normalise_locale(locale)
    for language in (normalised, normalised.split("-", 1)[0], DEFAULT_LOCALE):
        directory = root.joinpath(language)
        if directory.is_dir():
            return SummaryPrompts(
                version=version,
                language=language,
                instructions=_template(directory, "instructions.md"),
                call=_template(directory, "call.md"),
                answer=_template(directory, "answer.md"),
            )
    raise InvariantError(f"version {version!r} of the summary prompts has no {DEFAULT_LOCALE} text")


def _template(directory: Traversable, name: str) -> Template:
    template = Template(directory.joinpath(name).read_text(encoding="utf-8"))
    used = frozenset(template.get_identifiers())
    if not template.is_valid() or used != _PLACEHOLDERS[name]:
        raise InvariantError(
            f"the summary template {name} must use exactly the placeholders "
            f"{', '.join(sorted(_PLACEHOLDERS[name]))}; it uses {', '.join(sorted(used)) or 'none'}"
        )
    return template
