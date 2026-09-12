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

from letmehandle.domain.models.preferences import CallRules, UserPreferences

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.caller import CallerCategory
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.intent import CallImportance
    from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
    from letmehandle.domain.models.preferences import (
        DisclosableFact,
        Formality,
        HandlingPosture,
        ImportantContact,
        NotificationPreferences,
        TimeWindow,
        Topic,
        Verbosity,
    )
    from letmehandle.domain.ports.repositories import (
        OnboardingRepository,
        PreferencesRepository,
    )


@dataclass(frozen=True, slots=True)
class CallHandling:
    """The routing half of `CallRules`, as one screen sends it."""

    default_posture: HandlingPosture
    anonymous_posture: HandlingPosture
    posture_by_category: Mapping[CallerCategory, HandlingPosture]
    blocked_categories: frozenset[CallerCategory]
    escalate_at_or_above: CallImportance


@dataclass(frozen=True, slots=True)
class Hours:
    """The scheduling half. Both members are optional, and absent means "no window"."""

    working: TimeWindow | None = None
    quiet: TimeWindow | None = None


@dataclass(frozen=True, slots=True)
class PreferenceChanges:
    """What to change, with absent meaning "leave it alone".

    Every field defaults to `None`, and `None` means the caller said nothing about it. A field
    that genuinely needs clearing is cleared by sending the empty value for its type — an empty
    list of contacts, rather than an absent one.
    """

    locale: str | None = None
    # Call handling and hours are two screens and one domain object. They are carried
    # separately here, and recombined against what is stored, because building a whole
    # `CallRules` in the HTTP layer means the half that was not sent is filled from the
    # defaults — which resets it. A user who blocked spam callers and then set quiet hours
    # from a different screen would find the blocking silently gone.
    call_handling: CallHandling | None = None
    hours: Hours | None = None
    authority: AgentAuthority | None = None
    notifications: NotificationPreferences | None = None
    formality: Formality | None = None
    verbosity: Verbosity | None = None
    topics: frozenset[Topic] | None = None
    disclosable_facts: frozenset[DisclosableFact] | None = None
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
                "call_handling",
                "hours",
                "authority",
                "notifications",
                "formality",
                "verbosity",
                "topics",
                "disclosable_facts",
                "important_contacts",
            )
        )


def _merge_rules(current: CallRules, changes: PreferenceChanges) -> CallRules:
    """Rebuild the rules from what is stored and whichever half was sent.

    Here rather than in the HTTP layer, so that the answer to "what happens to the half nobody
    mentioned" is given once. The layer above sends what it has; it does not get to decide what
    the absence of the rest means.
    """
    handling = changes.call_handling
    hours = changes.hours

    return CallRules(
        default_posture=(current.default_posture if handling is None else handling.default_posture),
        anonymous_posture=(
            current.anonymous_posture if handling is None else handling.anonymous_posture
        ),
        posture_by_category=(
            dict(current.posture_by_category)
            if handling is None
            else dict(handling.posture_by_category)
        ),
        blocked_categories=(
            current.blocked_categories if handling is None else handling.blocked_categories
        ),
        escalate_at_or_above=(
            current.escalate_at_or_above if handling is None else handling.escalate_at_or_above
        ),
        working_hours=current.working_hours if hours is None else hours.working,
        quiet_hours=current.quiet_hours if hours is None else hours.quiet,
    )


class PreferencesService:
    """Preferences and onboarding progress, for one deployment."""

    def __init__(
        self, *, preferences: PreferencesRepository, onboarding: OnboardingRepository
    ) -> None:
        self._preferences = preferences
        self._onboarding = onboarding

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences:
        """What this user has chosen, or the defaults if they have chosen nothing.

        The defaults are returned rather than an absence, because every caller would otherwise
        substitute them itself and one of them would substitute something else.
        """
        stored = await self._preferences.get(user_id, for_update=for_update)
        return stored if stored is not None else UserPreferences()

    async def apply(self, user_id: UserId, changes: PreferenceChanges) -> UserPreferences:
        """Change the sections that were given, and leave the rest exactly as they were.

        The read is taken for update, because everything between it and the write is a decision
        made from what it returned. Two requests changing different sections would otherwise
        both start from the same values and the second would overwrite the first — and the
        client that does this is not a pathological one, it is a phone saving two screens.
        """
        current = await self.get(user_id, for_update=True)
        if changes.is_empty:
            return current

        updated = self._compose(current, changes)
        await self._preferences.save(user_id, updated)
        return updated

    async def replace_all(self, user_id: UserId, changes: PreferenceChanges) -> UserPreferences:
        """Store a complete set, built from the defaults.

        The deliberate counterpart to `apply`: a section this does not mention is reset rather
        than kept, so a caller that means to set everything says so instead of relying on
        having remembered every section.

        The only difference between the two is what they compose against — the defaults here,
        what is stored there — which is why the composing itself is shared.
        """
        replaced = self._compose(UserPreferences(), changes)
        await self._preferences.save(user_id, replaced)
        return replaced

    @staticmethod
    def _compose(base: UserPreferences, changes: PreferenceChanges) -> UserPreferences:
        """Everything from `base`, except the sections `changes` actually carries.

        Used by both `apply` and `replace_all`, and the only difference between them is what
        they pass as `base`: what is stored, or the defaults. Stating it once is what keeps the
        two from drifting into different answers about an absent section.
        """
        return replace(
            base,
            locale=base.locale if changes.locale is None else changes.locale,
            rules=_merge_rules(base.rules, changes),
            authority=base.authority if changes.authority is None else changes.authority,
            notifications=(
                base.notifications if changes.notifications is None else changes.notifications
            ),
            formality=base.formality if changes.formality is None else changes.formality,
            verbosity=base.verbosity if changes.verbosity is None else changes.verbosity,
            topics=base.topics if changes.topics is None else changes.topics,
            disclosable_facts=(
                base.disclosable_facts
                if changes.disclosable_facts is None
                else changes.disclosable_facts
            ),
            important_contacts=(
                base.important_contacts
                if changes.important_contacts is None
                else changes.important_contacts
            ),
        )

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
