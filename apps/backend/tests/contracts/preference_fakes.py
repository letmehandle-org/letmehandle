"""In-memory preference storage, for testing the use case without a database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.models.onboarding import OnboardingProgress
from letmehandle.domain.ports.repositories import (
    OnboardingRepository,
    PreferencesRepository,
)

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.preferences import UserPreferences


class InMemoryPreferencesRepository(PreferencesRepository):
    def __init__(self) -> None:
        self.by_user: dict[str, UserPreferences] = {}
        self.writes = 0

    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        # No await separates the read from the write, so `for_update` is accepted and ignored.
        return self.by_user.get(user_id.value)

    async def save(self, user_id: UserId, preferences: UserPreferences) -> None:
        self.by_user[user_id.value] = preferences
        # Counted so a test can prove that a request changing nothing costs no write.
        self.writes += 1


class InMemoryOnboardingRepository(OnboardingRepository):
    def __init__(self) -> None:
        self.by_user: dict[str, OnboardingProgress] = {}

    async def get(self, user_id: UserId) -> OnboardingProgress:
        return self.by_user.get(user_id.value, OnboardingProgress())

    async def save(self, user_id: UserId, progress: OnboardingProgress) -> None:
        self.by_user[user_id.value] = progress
