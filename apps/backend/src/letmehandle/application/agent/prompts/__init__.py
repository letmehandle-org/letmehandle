"""What the agent is told, from versioned template files, with the user and the call as data."""

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
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from importlib.resources.abc import Traversable
    from string import Template

    from letmehandle.application.preferences.context import PreferenceContext
    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.call import TranscriptEntry

# The prompt version every judgement uses; changing it needs an evaluation run.
PROMPT_VERSION: Final = "v4"

# Each template and exactly the placeholders it may use, checked when read.
_PLACEHOLDERS: Final = {
    "system.md": frozenset({"assessment_tool", "preferences"}),
    "transcript.md": frozenset({"transcript"}),
    "assessment.md": frozenset({"assessment_tool"}),
    "conversation.md": frozenset({"preferences", "situation"}),
    "greeting.md": frozenset(),
}


@dataclass(frozen=True, slots=True)
class Prompts:
    """One version of the agent's templates, in one language."""

    version: str
    language: str
    system: Template
    transcript: Template
    assessment: Template
    conversation: Template
    opening: Template | None

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
        return self.transcript.substitute(transcript=transcript_as_data(transcript))

    def assessment_request(self, *, assessment_tool: str) -> str:
        """What the model is told when it stops without recording an assessment."""
        return self.assessment.substitute(assessment_tool=assessment_tool)

    def conversation_context(
        self,
        preferences: PreferenceContext,
        authority: AgentAuthority,
        *,
        situation: Mapping[str, str],
    ) -> str:
        """What the voice on the call is told: the user and where reaching them stands."""
        return self.conversation.substitute(
            preferences=preferences_as_data(preferences, authority),
            situation=as_data(dict(situation)),
        )

    def greeting(self) -> str:
        """What the assistant says first on a call, before the caller has said anything."""
        if self.opening is None:
            raise InvariantError(f"version {self.version!r} of the agent prompts has no greeting")
        return self.opening.substitute().strip()


@lru_cache(maxsize=16)
def load_prompts(locale: str, version: str = PROMPT_VERSION) -> Prompts:
    """The templates of `version` closest to `locale`, narrowing to its language, then English."""
    return read_prompts(files(__name__), locale, version)


def read_prompts(templates: Traversable, locale: str, version: str) -> Prompts:
    """`load_prompts` from another directory, each template in its closest language."""
    found = TemplateVersion.open(
        templates, kind="agent prompts", locale=locale, version=version, placeholders=_PLACEHOLDERS
    )
    language, system = found.required("system.md")
    return Prompts(
        version=version,
        language=language,
        system=system,
        transcript=found.required("transcript.md")[1],
        assessment=found.required("assessment.md")[1],
        conversation=found.required("conversation.md")[1],
        opening=found.optional("greeting.md"),
    )


def preferences_as_data(context: PreferenceContext, authority: AgentAuthority) -> str:
    """The user's preferences as the model reads them, with permissions from `authority`."""
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
