"""Deleting an account: the person, and everything the product holds because of them.

Nearly all of it goes with the user's row, because every table holding something of theirs
references it. Two things do not. The sign-in challenges are kept by number, not by account, since
anybody may ask for a code; the ones sent to this number are the user's all the same. And a call
still going holds the user in memory and writes as it goes, so it is ended first and waited for:
deleting the rows under a live call would leave it to write a few more beneath nobody.
"""

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
        """End the user's calls, then remove the challenges sent to their number and the account.

        `calls` is None in a deployment that carries no calls, where none can be in progress.
        """
        if self._calls is not None:
            await self._calls.end_calls_of(user.id)
        await self._challenges.delete_for_number(user.phone_number)
        await self._users.delete(user.id)
