"""Reading and changing how somebody wants their calls handled.

The use case. It owns one rule that is easy to state and easy to get wrong: a section nobody
sent is left exactly as it was.

A partial update has to decide what an absent field means, and there are only two answers —
"leave it" or "clear it". Choosing per field, or leaving it to whatever the storage layer
happens to do, is how a screen that saves one section quietly erases the others. Here the
answer is stated once: the service reads what is stored, replaces only the sections it was
given, and writes the whole thing back.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from letmehandle.domain.models.preferences import UserPreferences

if TYPE_CHECKING:
    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.preferences import (
        CallRules,
        Formality,
        ImportantContact,
        NotificationPreferences,
        Topic,
        Verbosity,
    )
    from letmehandle.domain.ports.repositories import (
        OnboardingRepository,
        PreferencesRepository,
    )


@dataclass(frozen=True, slots=True)
class PreferenceChanges:
    """What to change, with absent meaning "leave it alone".

    Every field defaults to `None`, and `None` means the caller said nothing about it. A field
    that genuinely needs clearing is cleared by sending the empty value for its type — an empty
    list of contacts, rather than an absent one.
    """

    locale: str | None = None
    rules: CallRules | None = None
    authority: AgentAuthority | None = None
    notifications: NotificationPreferences | None = None
    formality: Formality | None = None
    verbosity: Verbosity | None = None
    topics: frozenset[Topic] | None = None
    important_contacts: tuple[ImportantContact, ...] | None = None

    @property
    def is_empty(self) -> bool:
        """Whether this asks for anything at all.

        A request that changes nothing is not an error — a client sending an untouched form is
        ordinary — but it should not cost a write.
        """
        return all(
            getattr(self, name) is None
            for name in (
                "locale",
                "rules",
                "authority",
                "notifications",
                "formality",
                "verbosity",
                "topics",
                "important_contacts",
            )
        )


class PreferencesService:
    """Preferences and onboarding progress, for one deployment."""

    def __init__(
        self, *, preferences: PreferencesRepository, onboarding: OnboardingRepository
    ) -> None:
        self._preferences = preferences
        self._onboarding = onboarding

    async def get(self, user_id: UserId) -> UserPreferences:
        """What this user has chosen, or the defaults if they have chosen nothing.

        The defaults are returned rather than an absence, because every caller would otherwise
        substitute them itself and one of them would substitute something else.
        """
        stored = await self._preferences.get(user_id)
        return stored if stored is not None else UserPreferences()

    async def has_chosen(self, user_id: UserId) -> bool:
        """Whether this user has ever saved anything.

        Distinct from holding the defaults: it is the difference between somebody who wants
        the defaults and somebody who has not been asked.
        """
        return await self._preferences.get(user_id) is not None

    async def apply(self, user_id: UserId, changes: PreferenceChanges) -> UserPreferences:
        """Change the sections that were given, and leave the rest exactly as they were."""
        current = await self.get(user_id)
        if changes.is_empty:
            return current

        updated = replace(
            current,
            locale=current.locale if changes.locale is None else changes.locale,
            rules=current.rules if changes.rules is None else changes.rules,
            authority=current.authority if changes.authority is None else changes.authority,
            notifications=(
                current.notifications if changes.notifications is None else changes.notifications
            ),
            formality=current.formality if changes.formality is None else changes.formality,
            verbosity=current.verbosity if changes.verbosity is None else changes.verbosity,
            topics=current.topics if changes.topics is None else changes.topics,
            important_contacts=(
                current.important_contacts
                if changes.important_contacts is None
                else changes.important_contacts
            ),
        )

        await self._preferences.save(user_id, updated)
        return updated

    async def replace_all(self, user_id: UserId, preferences: UserPreferences) -> UserPreferences:
        """Store a complete set, replacing everything.

        The deliberate counterpart to `apply`: a caller that means to set everything says so,
        rather than relying on having mentioned every section.
        """
        await self._preferences.save(user_id, preferences)
        return preferences

    # ------------------------------------------------------------- onboarding

    async def progress(self, user_id: UserId) -> OnboardingProgress:
        return await self._onboarding.get(user_id)

    async def record_step(
        self, user_id: UserId, step: OnboardingStep, *, skipped: bool = False
    ) -> OnboardingProgress:
        """Record a step as answered or deliberately passed over."""
        current = await self._onboarding.get(user_id)
        updated = current.skipping(step) if skipped else current.completing(step)
        await self._onboarding.save(user_id, updated)
        return updated
