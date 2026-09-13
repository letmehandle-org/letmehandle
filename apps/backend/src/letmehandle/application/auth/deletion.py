"""Deleting an account: its live calls, the codes sent to its number, and its rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.user import User
    from letmehandle.domain.ports.repositories import OTPChallengeRepository, UserRepository


class LiveCalls(Protocol):
    """The calls in progress, as account deletion needs them. The orchestrator is this."""

    async def end_calls_of(self, user_id: UserId) -> None:
        """End every live call of this user's and return once none is left, within a bound."""


class AccountDeletion:
    """Deletes a user's account and everything tied to it."""

    def __init__(
        self,
        *,
        users: UserRepository,
        challenges: OTPChallengeRepository,
        calls: LiveCalls | None,
    ) -> None:
        self._users = users
        self._challenges = challenges
        self._calls = calls

    async def delete(self, user: User) -> None:
        """End the user's calls, then remove the challenges sent to their number and the account."""
        if self._calls is not None:
            await self._calls.end_calls_of(user.id)
        await self._challenges.delete_for_number(user.phone_number)
        await self._users.delete(user.id)
