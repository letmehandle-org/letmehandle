"""What the agent is told, read from versioned template files.

The words live in files under a version and a language — `v1/en/system.md` — rather than in code,
so that a change to what the model is told is a change somebody can read as prose, review as a
diff, and measure with the evaluation suite before it ships. A new version is a new directory; an
old one stays, so a stored judgement can be read against the words that produced it.

Nothing about a particular user is written in a template. The user arrives through the preference
context (Phase 3), rendered here as data, and the caller arrives as a transcript rendered the same
way and sent in a message of its own. Neither is ever pasted into the instructions as prose.

Both are rendered as JSON with the angle brackets escaped. The delimiters around them are the only
`<transcript>` and `</transcript>` the model sees, however hard somebody on the line tries to say
one: a caller who speaks a closing tag produces an escaped string inside the data, not the end of
it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from enum import IntEnum, StrEnum
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

    def system_prompt(self, preferences: PreferenceContext, *, assessment_tool: str) -> str:
        """The instructions, with the user's preferences attached as data."""
        return self.system.substitute(
            assessment_tool=assessment_tool, preferences=as_data(preferences)
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
    rendered = json.dumps(_plain(value), ensure_ascii=False, indent=2)
    return rendered.replace("<", "\\u003c").replace(">", "\\u003e")


def _plain(value: object) -> object:
    """Values JSON can carry, with every enumeration written as the word a model is shown.

    An importance goes by its name rather than its number: `notable` is something a model can
    compare a call against, and `40` is not.
    """
    if isinstance(value, IntEnum):
        return value.name.lower()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return value
