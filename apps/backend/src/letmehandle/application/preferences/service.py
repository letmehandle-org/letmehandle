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
    """The half of the voice selection a user picks from a catalogue.

    Carried on its own rather than as a whole `VoiceSelection`, for the reason call handling
    and hours are carried apart: the other half is a cloned voice, which the screen making this
    change knows nothing about. A caller that had to build a whole selection would first have
    to read the stored one — outside whatever lock the write takes — and would then write back
    a cloned voice that may have been revoked in between.
    """

    voice_id: str | None = None


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
    # defaults — which resets it. A user who blocked spam callers and then set their hours
    # from a different screen would find the blocking silently gone.
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
        # A stored set may hold more than the ceiling; a change may not ask for it.
        if self.transcript_retention_days is not None:
            check_retention_choice(self.transcript_retention_days)

    @property
    def is_empty(self) -> bool:
        """Whether this asks for anything at all.

        A request that changes nothing is not an error — a client sending an untouched form is
        ordinary — but it should not cost a write.
        """
        return all(getattr(self, each.name) is None for each in fields(self))


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
        active_hours=current.active_hours if hours is None else hours.active,
    )


def _merge_voice(current: VoiceSelection, changes: PreferenceChanges) -> VoiceSelection:
    """The stored selection with whichever half was sent replaced.

    The cloned voice is never carried through a request, so it is read here, under the same
    lock as the write. That is the whole point: a clone revoked while this request was in
    flight stays revoked instead of being written back from a read taken before it.
    """
    persona = changes.persona_voice
    return VoiceSelection(
        cloned_voice_id=current.cloned_voice_id,
        persona_voice_id=current.persona_voice_id if persona is None else persona.voice_id,
    )


class PreferencesService:
    """Preferences and onboarding progress, for one deployment.

    `onboarding_flow` is which steps this deployment asks. It is handed in, decided from what the
    deployment can do, so nothing here learns how calls arrive.
    """

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

        The chosen voice is the exception, and survives. It is chosen through a different route,
        because choosing one needs the provider's catalogue to validate against, so a replace
        request has no way to carry it — and a field a caller cannot send is a field a caller
        cannot have meant to clear. Without this, replacing every other preference silently
        changes how the assistant sounds, and nothing in the request says so.
        """
        current = await self.get(user_id, for_update=True)
        replaced = self._compose(replace(UserPreferences(), voice=current.voice), changes)
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
            # Composing produces today's shape, whatever shape the stored row was in, so
            # the set that is handed back says the same thing the row it is about to
            # become will say.
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
        """Record a step as answered or deliberately passed over.

        A step this deployment does not ask is refused before anything is stored.
        """
        current = await self.progress(user_id)
        updated = current.skipping(step) if skipped else current.completing(step)
        await self._onboarding.save(user_id, updated.progress)
        return updated
