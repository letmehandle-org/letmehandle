"""Storage, as interfaces the domain owns.

Every method that reads something belonging to a person takes the owner's identifier. There is
no method on these interfaces that can return another user's row by accident, which is the
cheapest possible defence against the most common mistake in a multi-user system: a query that
filters by everything except who it belongs to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.user import User
    from letmehandle.domain.ports.notification import DeviceToken


class UserRepository(ABC):
    """People with accounts."""

    @abstractmethod
    async def get(self, user_id: UserId) -> User | None:
        """The user, or nothing. Nothing is an ordinary answer, not an error."""

    @abstractmethod
    async def find_by_number(self, number: PhoneNumber) -> User | None:
        """The account for this number, if there is one."""

    @abstractmethod
    async def add(self, user: User) -> None:
        """Store a new account."""

    @abstractmethod
    async def update(self, user: User) -> None:
        """Store changes to an existing one."""


class OTPChallengeRepository(ABC):
    """Outstanding proofs of control over a number."""

    @abstractmethod
    async def add(self, challenge: OTPChallenge) -> None:
        """Store a newly issued challenge."""

    @abstractmethod
    async def get(self, challenge_id: str) -> OTPChallenge | None:
        """The challenge, or nothing. Nothing is what an invented identifier gets."""

    @abstractmethod
    async def update(self, challenge: OTPChallenge) -> None:
        """Store a changed challenge: one more attempt used, or one that has been verified."""

    @abstractmethod
    async def count_issued_since(self, number: PhoneNumber, since: datetime) -> int:
        """How many challenges this number has been sent lately.

        The basis of the rate limit. Counted per number rather than per account, because an
        attacker enumerating numbers has no account.
        """

    @abstractmethod
    async def delete_expired(self, before: datetime) -> int:
        """Remove what is no longer useful, returning how many went.

        A challenge past its expiry can never succeed, and keeping it is keeping a hash of a
        credential for no reason.
        """


class RefreshTokenRepository(ABC):
    """Long-lived credentials, and the families they belong to."""

    @abstractmethod
    async def add(self, token: RefreshToken) -> None:
        """Store a newly issued token."""

    @abstractmethod
    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Look a token up by its hash. The raw token is never stored to look up by."""

    @abstractmethod
    async def update(self, token: RefreshToken) -> None:
        """Store a changed token: one that has been rotated, or revoked."""

    @abstractmethod
    async def revoke_family(self, family_id: str, at_instant: datetime) -> int:
        """Revoke every token descended from one sign-in, returning how many.

        Called when a rotated token is presented again. Either the user's copy was stolen or
        the thief's was, and nothing can tell which, so the only safe answer is to end the
        family and make the legitimate user sign in again.
        """

    @abstractmethod
    async def revoke_all_for_user(self, user_id: UserId, at_instant: datetime) -> int:
        """Sign the user out everywhere."""


class DeviceRepository(ABC):
    """Where a user's notifications can be delivered."""

    @abstractmethod
    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        """Record a device, replacing any earlier registration of the same token.

        Replacing rather than adding: a token can move between accounts when a handset changes
        hands, and two accounts sharing one would send somebody else's call context to it.
        """

    @abstractmethod
    async def tokens_for(self, user_id: UserId) -> list[DeviceToken]:
        """Every device this user has registered."""

    @abstractmethod
    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        """Forget a device, on sign-out or when a platform reports the token dead."""
