"""Versioned template files, read by the closest language, and the data rendered into them."""

from __future__ import annotations

import json
from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING

from letmehandle.application.preferences.context import DEFAULT_LOCALE, normalise_locale
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from importlib.resources.abc import Traversable

    from letmehandle.domain.models.call import TranscriptEntry


@dataclass(frozen=True, slots=True)
class TemplateVersion:
    """One version of a set of templates, each read from the closest language that has it."""

    kind: str
    version: str
    root: Traversable
    languages: tuple[str, ...]
    placeholders: Mapping[str, frozenset[str]]

    @classmethod
    def open(
        cls,
        templates: Traversable,
        *,
        kind: str,
        locale: str,
        version: str,
        placeholders: Mapping[str, frozenset[str]],
    ) -> TemplateVersion:
        """`version` of `templates`, narrowing `locale` to its language and then English."""
        root = templates.joinpath(version)
        if not root.is_dir():
            raise InvariantError(f"there are no {kind} of version {version!r}")
        normalised = normalise_locale(locale)
        languages = (normalised, normalised.split("-", 1)[0], DEFAULT_LOCALE)
        return cls(kind, version, root, languages, placeholders)

    def closest(self, name: str) -> tuple[str, Template] | None:
        """The template `name` and its language, or None when no language has it."""
        for language in self.languages:
            directory = self.root.joinpath(language)
            if directory.joinpath(name).is_file():
                return language, self._checked(directory.joinpath(name), name)
        return None

    def required(self, name: str) -> tuple[str, Template]:
        """The template `name` and its language, refused when English lacks it too."""
        found = self.closest(name)
        if found is None:
            raise InvariantError(
                f"version {self.version!r} of the {self.kind} has no {DEFAULT_LOCALE} text"
            )
        return found

    def optional(self, name: str) -> Template | None:
        """The template `name`, or None for a version written without it."""
        found = self.closest(name)
        return None if found is None else found[1]

    def _checked(self, file: Traversable, name: str) -> Template:
        template = Template(file.read_text(encoding="utf-8"))
        expected = self.placeholders[name]
        used = frozenset(template.get_identifiers())
        if not template.is_valid() or used != expected:
            raise InvariantError(
                f"the {self.kind} template {name} must use exactly the placeholders "
                f"{', '.join(sorted(expected))}; it uses {', '.join(sorted(used)) or 'none'}"
            )
        return template


def as_data(value: object) -> str:
    """JSON for a model to read, with nothing in it that could close a delimiter around it."""
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return rendered.replace("<", "\\u003c").replace(">", "\\u003e")


def transcript_as_data(transcript: Sequence[TranscriptEntry]) -> str:
    """What was said, speaker by speaker, as data."""
    return as_data([{"speaker": entry.speaker.value, "text": entry.text} for entry in transcript])
