"""What the agent is told, read from versioned template files.

The words live in files under a version and a language — `v1/en/system.md` — rather than in code,
so that a change to what the model is told is a change somebody can read as prose, review as a
diff, and measure with the evaluation suite before it ships. A new version is a new directory; an
old one stays, so a stored judgement can be read against the words that produced it.

Nothing about a particular user is written in a template. The user arrives through the preference
context (Phase 3), rendered here as data, and the caller arrives as a transcript rendered the same
way and sent in a message of its own. Neither is ever pasted into the instructions as prose.

The preferences are rendered by `preferences_as_data` and by nothing else: the system prompt and the
`get_user_preferences` tool both show the model its bytes, so the two can never describe the user
differently. What the assistant may and may not do is said from the grant the tools enforce.

Everything is rendered as JSON with the angle brackets escaped. The delimiters around the data are
the only `<transcript>` and `</transcript>` the model sees, however hard somebody on the line tries
to say one: a caller who speaks a closing tag produces an escaped string inside the data, not the
end of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from string import Template
from typing import TYPE_CHECKING, Final

from letmehandle.application.preferences.context import DEFAULT_LOCALE, normalise_locale
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.resources.abc import Traversable

    from letmehandle.application.preferences.context import PreferenceContext
    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.call import TranscriptEntry

# The version every judgement uses unless told otherwise. Changing it is a prompt change, and the
# evaluation suite is how a prompt change earns its place.
PROMPT_VERSION: Final = "v1"

# Each template, and exactly the placeholders it may use. Checked when the templates are read, so
# a stray `$` or a misspelt placeholder stops a process from loading them rather than failing on
# somebody's call.
_PLACEHOLDERS: Final = {
    "system.md": frozenset({"assessment_tool", "preferences"}),
    "transcript.md": frozenset({"transcript"}),
    "assessment.md": frozenset({"assessment_tool"}),
}


@dataclass(frozen=True, slots=True)
class Prompts:
    """One version of the agent's templates, in one language."""

    version: str
    language: str
    system: Template
    transcript: Template
    assessment: Template

    def system_prompt(
        self, preferences: PreferenceContext, authority: AgentAuthority, *, assessment_tool: str
    ) -> str:
        """The instructions, with the user's preferences attached as data."""
        return self.system.substitute(
            assessment_tool=assessment_tool,
            preferences=preferences_as_data(preferences, authority),
        )

    def transcript_message(self, transcript: Sequence[TranscriptEntry]) -> str:
        """The call so far, delimited and labelled as what was said rather than what to do."""
        spoken = [{"speaker": entry.speaker.value, "text": entry.text} for entry in transcript]
        return self.transcript.substitute(transcript=as_data(spoken))

    def assessment_request(self, *, assessment_tool: str) -> str:
        """What the model is told when it stops without recording an assessment."""
        return self.assessment.substitute(assessment_tool=assessment_tool)


@lru_cache(maxsize=16)
def load_prompts(locale: str, version: str = PROMPT_VERSION) -> Prompts:
    """The templates of `version` closest to `locale`, narrowing to its language, then English.

    The closest rather than an exact match, for the reason the phrasebook gives: which language a
    template was written in and which language the user speaks are different questions, and a
    user whose language has no templates yet still gets a judgement.
    """
    return read_prompts(files(__name__), locale, version)


def read_prompts(templates: Traversable, locale: str, version: str) -> Prompts:
    """`load_prompts`, from a directory of versions other than the one shipped with the code."""
    root = templates.joinpath(version)
    if not root.is_dir():
        raise InvariantError(f"there are no agent prompts of version {version!r}")

    normalised = normalise_locale(locale)
    for language in (normalised, normalised.split("-", 1)[0], DEFAULT_LOCALE):
        directory = root.joinpath(language)
        if directory.is_dir():
            return Prompts(
                version=version,
                language=language,
                system=_template(directory, "system.md"),
                transcript=_template(directory, "transcript.md"),
                assessment=_template(directory, "assessment.md"),
            )
    raise InvariantError(f"version {version!r} of the agent prompts has no {DEFAULT_LOCALE} text")


def _template(directory: Traversable, name: str) -> Template:
    template = Template(directory.joinpath(name).read_text(encoding="utf-8"))
    used = frozenset(template.get_identifiers())
    if not template.is_valid() or used != _PLACEHOLDERS[name]:
        raise InvariantError(
            f"the prompt template {name} must use exactly the placeholders "
            f"{', '.join(sorted(_PLACEHOLDERS[name]))}; it uses {', '.join(sorted(used)) or 'none'}"
        )
    return template


def as_data(value: object) -> str:
    """JSON for a model to read, with nothing in it that could close a delimiter around it."""
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return rendered.replace("<", "\\u003c").replace(">", "\\u003e")


def preferences_as_data(context: PreferenceContext, authority: AgentAuthority) -> str:
    """The user's preferences as the model reads them, wherever it reads them.

    Deterministic, over tuples the context already sorted, and free of any phone number because the
    context is. What the assistant may do is read from `authority`, the grant the tools enforce,
    rather than from the copy the context was built with: a model told it may do something a tool
    then refuses is a model that promises the caller something and has to take it back. An
    importance goes by its name rather than its number, because `notable` is something a model can
    compare a call against and `40` is not.
    """
    return as_data(
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
            "within_the_users_active_hours": context.in_active_hours,
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
        }
    )
