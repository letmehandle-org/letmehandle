"""Reading and changing preferences, where an absent section is left as it was (D-023)."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING

from letmehandle.domain.models.onboarding import Onboarding
from letmehandle.domain.models.preferences import (
    PREFERENCES_VERSION,
    CallRules,
    UserPreferences,
    check_retention_choice,
)
from letmehandle.domain.models.voice import VoiceSelection

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.domain.models.authority import AgentAuthority
    from letmehandle.domain.models.caller import CallerCategory
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.intent import CallImportance
    from letmehandle.domain.models.onboarding import OnboardingFlow, OnboardingStep
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
    """The scheduling half: when the assistant works. No window means around the clock (D-030)."""

    active: TimeWindow | None = None


@dataclass(frozen=True, slots=True)
class PersonaVoice:
    """The half of the voice selection a user picks from a catalogue."""

    voice_id: str | None = None


@dataclass(frozen=True, slots=True)
class PreferenceChanges:
    """What to change: `None` leaves a field alone, and its empty value clears it."""

    locale: str | None = None
    # The two halves of `CallRules`, each merged against what is stored.
    call_handling: CallHandling | None = None
    hours: Hours | None = None
    authority: AgentAuthority | None = None
    notifications: NotificationPreferences | None = None
    persona_voice: PersonaVoice | None = None
    formality: Formality | None = None
    verbosity: Verbosity | None = None
    topics: frozenset[Topic] | None = None
    disclosable_facts: frozenset[DisclosableFact] | None = None
    important_contacts: tuple[ImportantContact, ...] | None = None
    transcript_retention_days: int | None = None

    def __post_init__(self) -> None:
        if self.transcript_retention_days is not None:
            check_retention_choice(self.transcript_retention_days)

    @property
    def is_empty(self) -> bool:
        """Whether this asks for anything at all."""
        return all(getattr(self, each.name) is None for each in fields(self))


def _merge_rules(current: CallRules, changes: PreferenceChanges) -> CallRules:
    """Rebuild the rules from what is stored and whichever half was sent."""
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
        active_hours=current.active_hours if hours is None else hours.active,
    )


def _merge_voice(current: VoiceSelection, changes: PreferenceChanges) -> VoiceSelection:
    """The stored selection with the persona half replaced; the clone is read under the lock."""
    persona = changes.persona_voice
    return VoiceSelection(
        cloned_voice_id=current.cloned_voice_id,
        persona_voice_id=current.persona_voice_id if persona is None else persona.voice_id,
    )


class PreferencesService:
    """Preferences and onboarding progress, over the steps this deployment asks."""

    def __init__(
        self,
        *,
        preferences: PreferencesRepository,
        onboarding: OnboardingRepository,
        onboarding_flow: OnboardingFlow,
    ) -> None:
        self._preferences = preferences
        self._onboarding = onboarding
        self._flow = onboarding_flow

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences:
        """What this user has chosen, or the defaults if they have chosen nothing."""
        stored = await self._preferences.get(user_id, for_update=for_update)
        return stored if stored is not None else UserPreferences()

    async def apply(self, user_id: UserId, changes: PreferenceChanges) -> UserPreferences:
        """Change the sections given and leave the rest, reading under a lock until the write."""
        current = await self.get(user_id, for_update=True)
        if changes.is_empty:
            return current

        updated = self._compose(current, changes)
        await self._preferences.save(user_id, updated)
        return updated

    async def replace_all(self, user_id: UserId, changes: PreferenceChanges) -> UserPreferences:
        """Store a complete set built from the defaults, keeping the stored voice selection."""
        current = await self.get(user_id, for_update=True)
        replaced = self._compose(replace(UserPreferences(), voice=current.voice), changes)
        await self._preferences.save(user_id, replaced)
        return replaced

    @staticmethod
    def _compose(base: UserPreferences, changes: PreferenceChanges) -> UserPreferences:
        """Everything from `base`, except the sections `changes` actually carries."""
        return replace(
            base,
            version=PREFERENCES_VERSION,
            locale=base.locale if changes.locale is None else changes.locale,
            rules=_merge_rules(base.rules, changes),
            authority=base.authority if changes.authority is None else changes.authority,
            notifications=(
                base.notifications if changes.notifications is None else changes.notifications
            ),
            voice=_merge_voice(base.voice, changes),
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
            transcript_retention_days=(
                base.transcript_retention_days
                if changes.transcript_retention_days is None
                else changes.transcript_retention_days
            ),
        )

    # ------------------------------------------------------------- onboarding

    async def progress(self, user_id: UserId) -> Onboarding:
        """Where this user is, among the steps this deployment asks."""
        return Onboarding(flow=self._flow, progress=await self._onboarding.get(user_id))

    async def record_step(
        self, user_id: UserId, step: OnboardingStep, *, skipped: bool = False
    ) -> Onboarding:
        """Record a step as answered or skipped; one this deployment does not ask is refused."""
        current = await self.progress(user_id)
        updated = current.skipping(step) if skipped else current.completing(step)
        await self._onboarding.save(user_id, updated.progress)
        return updated
