"""Storage, as interfaces the domain owns.

Every method that reads something belonging to a person takes the owner's identifier. There is
no method on these interfaces that can return another user's row by accident, which is the
cheapest possible defence against the most common mistake in a multi-user system: a query that
filters by everything except who it belongs to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
    from letmehandle.domain.models.call import CallSession, TranscriptEntry
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.models.onboarding import OnboardingProgress
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.preferences import UserPreferences
    from letmehandle.domain.models.summary import CallSummary
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


class PreferencesRepository(ABC):
    """How each user wants their calls handled.

    Every method takes the user whose preferences they are. There is no way to ask this
    interface for "the preferences", because preferences belong to somebody and a query that
    forgets whose is the most expensive mistake available in a multi-user system.
    """

    @abstractmethod
    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        """What this user has chosen, or nothing if they have chosen nothing yet.

        Nothing is distinct from the defaults on purpose: a caller that cannot tell them apart
        cannot tell a user who wants the defaults from one who has not been asked.

        `for_update` says this read is the first half of a read-modify-write, and asks the
        store to hold the row until the transaction ends. Without it two requests changing
        different sections both read the same starting point and the second overwrites the
        first — which is not a rare race: a client saving two screens in quick succession hits
        it, and measured here it lost the earlier change fourteen times in fifteen.

        An implementation with nothing to lock may ignore it. One that cannot honour it must
        say so rather than pretending.
        """

    @abstractmethod
    async def save(self, user_id: UserId, preferences: UserPreferences) -> None:
        """Store the whole set, replacing whatever was there.

        Whole rather than partial. A partial write has to decide what an absent field means,
        and it will eventually decide wrongly; the application layer composes the new set from
        the old one and writes all of it.
        """


class OnboardingRepository(ABC):
    """How far through setting up each user is."""

    @abstractmethod
    async def get(self, user_id: UserId) -> OnboardingProgress:
        """Where this user is. Somebody who has never started is at the beginning.

        Returns progress rather than `None`, because "has not started" is a real position in
        the flow rather than an absence — and a caller that has to handle `None` will forget.
        """

    @abstractmethod
    async def save(self, user_id: UserId, progress: OnboardingProgress) -> None:
        """Record where they have reached."""


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


# The most calls one page of history can hold. A client asking for everything at once is a
# query whose cost grows with the account's age, and a phone that renders it slowly for ever.
MAX_CALL_PAGE: Final = 100

# The most entries one purge statement deletes, and the most users one page of candidates holds.
# Bounded so a statement's locks and a transaction's length do not grow with the backlog.
MAX_PURGE_BATCH: Final = 10_000


def check_page_size(limit: int, maximum: int) -> int:
    """The size, once it is known to be at least one and at most the maximum."""
    if not 1 <= limit <= maximum:
        raise InvariantError(f"a page holds between 1 and {maximum} items, not {limit}")
    return limit


@dataclass(frozen=True, slots=True)
class CallCursor:
    """Where the previous page of history ended: the last call it held.

    Both fields, because two calls can start in the same instant, and a cursor on the time alone
    would skip one of them or show it twice.
    """

    started_at: datetime
    call_id: CallId


@dataclass(frozen=True, slots=True)
class CallPage:
    """Some of a user's calls, newest first, and where the next page starts if there is one."""

    calls: tuple[CallSession, ...]
    next_cursor: CallCursor | None


class CallRepository(ABC):
    """Calls, durably, so that a restart mid-call does not lose the record.

    What is stored is the call's state, its caller, its participants and its timing. What is
    said on it is not: that belongs to `TranscriptRepository`, encrypted and on its own clock.
    """

    @abstractmethod
    async def save(self, call: CallSession) -> None:
        """Store the call as it now stands, creating it the first time.

        Raises `RecordNotFoundError` when the identifier already belongs to another user's call,
        and changes nothing: one user's write can never land on another user's record.
        """

    @abstractmethod
    async def get(self, user_id: UserId, call_id: CallId) -> CallSession | None:
        """This user's call, or nothing — which is also the answer for somebody else's."""

    @abstractmethod
    async def list_for_user(
        self, user_id: UserId, *, limit: int, after: CallCursor | None = None
    ) -> CallPage:
        """A page of this user's calls, newest first.

        `limit` is between 1 and `MAX_CALL_PAGE`; anything else raises `InvariantError` rather
        than being quietly clamped, because a caller asking for a thousand has a bug to hear
        about. `after` is the previous page's `next_cursor`.
        """


class TranscriptRepository(ABC):
    """What was said on calls, encrypted at rest (D-014).

    Plaintext crosses this interface and nothing below it: an implementation encrypts before
    anything is written and decrypts after it is read.
    """

    @abstractmethod
    async def append(
        self, user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
    ) -> None:
        """Add entries to this user's call.

        Raises `RecordNotFoundError` when the call is not this user's, before writing anything.
        """

    @abstractmethod
    async def for_call(self, user_id: UserId, call_id: CallId) -> tuple[TranscriptEntry, ...]:
        """What remains of this user's call's transcript, in the order it was said.

        Empty for a call that is not theirs, and for one whose transcript has been purged; the
        two are indistinguishable by design. Raises `InvariantError` for a transcript with a
        line missing from its middle or present twice, rather than returning what is left.
        """


class TranscriptRetentionRepository(ABC):
    """Deleting transcript entries that have outlived their owner's retention.

    Apart from `TranscriptRepository` because it never needs to read what was said. The purge
    holds this and nothing that can decrypt, so a scheduled job with database access is not also
    a job that can read every transcript in the system.
    """

    @abstractmethod
    async def users_with_entries_at_or_before(
        self, cutoff: datetime, *, after: UserId | None, limit: int
    ) -> list[UserId]:
        """Users holding at least one entry said at or before `cutoff`, in identifier order.

        The purge's list of who to look at. `after` is the last user of the previous page.
        """

    @abstractmethod
    async def delete_expired(self, user_id: UserId, *, at_or_before: datetime, limit: int) -> int:
        """Delete up to `limit` of this user's entries said at or before the instant.

        Returns how many this call deleted, which is never a row another caller deleted: two
        purges running together each count only their own.
        """


class SummaryRepository(ABC):
    """The structured record of each call, which outlives its transcript."""

    @abstractmethod
    async def add(self, user_id: UserId, summary: CallSummary) -> None:
        """Write the summary, once, at completion.

        Raises `RecordNotFoundError` when the call is not this user's and
        `AlreadyRecordedError` when it already has a summary.
        """

    @abstractmethod
    async def get(self, user_id: UserId, call_id: CallId) -> CallSummary | None:
        """This user's summary of the call, or nothing."""
