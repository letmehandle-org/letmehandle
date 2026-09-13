"""Storage interfaces the domain owns, scoping every read of a person's data by its owner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from letmehandle.domain.models.auth import OTPChallenge, RefreshToken
    from letmehandle.domain.models.call import CallSession, TranscriptEntry
    from letmehandle.domain.models.escalation_context import (
        EscalationContext,
        NotificationDelivery,
    )
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.models.onboarding import OnboardingProgress
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.preferences import UserPreferences
    from letmehandle.domain.models.summary import CallOutcome, CallSummary
    from letmehandle.domain.models.timeline import CallOutline, TimelineMark
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

    @abstractmethod
    async def delete(self, user_id: UserId) -> None:
        """Remove the account and everything stored for it. Nothing there is not an error."""


class OTPChallengeRepository(ABC):
    """Outstanding proofs of control over a number."""

    @abstractmethod
    async def add(self, challenge: OTPChallenge) -> None:
        """Store a newly issued challenge."""

    @abstractmethod
    async def get(self, challenge_id: str) -> OTPChallenge | None:
        """The challenge, or nothing, locked for the rest of the unit of work."""

    @abstractmethod
    async def update(self, challenge: OTPChallenge) -> None:
        """Store a changed challenge: one more attempt used, or one that has been verified."""

    @abstractmethod
    async def issued_since(self, number: PhoneNumber, since: datetime) -> list[datetime]:
        """When this number was sent each challenge since `since`, oldest first."""

    @abstractmethod
    async def failed_attempts_since(self, number: PhoneNumber, since: datetime) -> int:
        """Wrong codes entered for this number across every challenge issued since then."""

    @abstractmethod
    async def supersede_open(self, number: PhoneNumber, instant: datetime) -> int:
        """Close every challenge still open for this number, returning how many were closed."""

    @abstractmethod
    async def count_all_issued_since(self, since: datetime, calling_code: str | None = None) -> int:
        """Challenges sent to anybody since then, or only to numbers with this calling code."""

    @abstractmethod
    async def delete_expired(self, before: datetime) -> int:
        """Remove challenges expired before `before`, returning how many went."""

    @abstractmethod
    async def delete_for_number(self, number: PhoneNumber) -> None:
        """Remove every challenge sent to this number, which is who they say asked for one."""


class RefreshTokenRepository(ABC):
    """Long-lived credentials, and the families they belong to."""

    @abstractmethod
    async def add(self, token: RefreshToken) -> None:
        """Store a newly issued token."""

    @abstractmethod
    async def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """The token with this hash, locked for the rest of the unit of work."""

    @abstractmethod
    async def update(self, token: RefreshToken) -> None:
        """Store a changed token: one that has been rotated, or revoked."""

    @abstractmethod
    async def revoke_family(self, family_id: str, at_instant: datetime) -> int:
        """Revoke every token descended from one sign-in, returning how many."""

    @abstractmethod
    async def revoke_all_for_user(self, user_id: UserId, at_instant: datetime) -> int:
        """Sign the user out everywhere."""


class PreferencesRepository(ABC):
    """How each user wants their calls handled, always asked for by whose they are."""

    @abstractmethod
    async def get(self, user_id: UserId, *, for_update: bool = False) -> UserPreferences | None:
        """The user's choices or nothing; `for_update` locks the row until the transaction ends."""

    @abstractmethod
    async def save(self, user_id: UserId, preferences: UserPreferences) -> None:
        """Store the whole set, replacing whatever was there."""


class OnboardingRepository(ABC):
    """How far through setting up each user is."""

    @abstractmethod
    async def get(self, user_id: UserId) -> OnboardingProgress:
        """Where this user is; somebody who never started is at the beginning, not `None`."""

    @abstractmethod
    async def save(self, user_id: UserId, progress: OnboardingProgress) -> None:
        """Record where they have reached."""


class DeviceRepository(ABC):
    """Where a user's notifications can be delivered."""

    @abstractmethod
    async def register(self, user_id: UserId, token: DeviceToken) -> None:
        """Record a device, replacing any registration of the same token, whoever's it was."""

    @abstractmethod
    async def tokens_for(self, user_id: UserId) -> list[DeviceToken]:
        """Every device this user has registered."""

    @abstractmethod
    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        """Forget a device, on sign-out or when a platform reports the token dead."""


# The most calls one page of history can hold.
MAX_CALL_PAGE: Final = 100

# The most entries one purge statement deletes, and the most users one page of candidates holds.
MAX_PURGE_BATCH: Final = 10_000


def check_page_size(limit: int, maximum: int) -> int:
    """The size, once it is known to be at least one and at most the maximum."""
    if not 1 <= limit <= maximum:
        raise InvariantError(f"a page holds between 1 and {maximum} items, not {limit}")
    return limit


@dataclass(frozen=True, slots=True)
class CallCursor:
    """Where the previous page of history ended: the start and id of its last call."""

    started_at: datetime
    call_id: CallId


@dataclass(frozen=True, slots=True)
class CallPage:
    """Some of a user's calls, newest first, and where the next page starts if there is one."""

    calls: tuple[CallSession, ...]
    next_cursor: CallCursor | None


@dataclass(frozen=True, slots=True)
class CallFilter:
    """Which of a user's calls a page holds: aware bounds, from inclusive, before exclusive."""

    outcome: CallOutcome | None = None
    started_from: datetime | None = None
    started_before: datetime | None = None
    human_joined: bool | None = None

    def __post_init__(self) -> None:
        bounds = [bound for bound in (self.started_from, self.started_before) if bound is not None]
        if any(bound.tzinfo is None for bound in bounds):
            raise InvariantError("a range of calls is bounded by times that know their timezone")
        if len(bounds) == 2 and bounds[0] >= bounds[1]:
            raise InvariantError("a range of calls starts before it ends")


class CallRepository(ABC):
    """Calls' state, caller, participants and timing, durably; what was said is stored apart."""

    @abstractmethod
    async def save(self, call: CallSession) -> None:
        """Store the call as it stands, refusing another user's id or reopening an ended call."""

    @abstractmethod
    async def get(self, user_id: UserId, call_id: CallId) -> CallSession | None:
        """This user's call, or nothing — which is also the answer for somebody else's."""

    @abstractmethod
    async def list_for_user(
        self,
        user_id: UserId,
        *,
        limit: int,
        after: CallCursor | None = None,
        matching: CallFilter | None = None,
    ) -> CallPage:
        """A page of this user's matching calls, newest first, `limit` from 1 to `MAX_CALL_PAGE`."""

    @abstractmethod
    async def unfinished(self, *, limit: int) -> tuple[CallSession, ...]:
        """Up to `limit` unended calls of every user, oldest first, for a process starting up."""

    @abstractmethod
    async def delete(self, user_id: UserId, call_id: CallId) -> None:
        """Delete this user's call and all recorded about it, together; absent is a no-op."""


class TranscriptStatus(StrEnum):
    """Whether a call's transcript can still be read, and if not, why not."""

    RETAINED = "retained"
    PURGED = "purged"
    NOT_RECORDED = "not_recorded"


class CallTimelineRepository(ABC):
    """Each call's timeline marks, written and deleted with the call, and its outline."""

    @abstractmethod
    async def append(self, call_id: CallId, marks: Sequence[TimelineMark]) -> None:
        """Add marks to a stored call's timeline, after those already there."""

    @abstractmethod
    async def outline(self, call_id: CallId) -> CallOutline | None:
        """The call's structure and marks in order, or nothing; unscoped, identifying nobody."""


class TranscriptRepository(ABC):
    """What was said on calls, encrypted below this interface (D-014)."""

    @abstractmethod
    async def append(
        self, user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
    ) -> None:
        """Add entries to this user's call, or raise `RecordNotFoundError` before writing any."""

    @abstractmethod
    async def for_call(self, user_id: UserId, call_id: CallId) -> tuple[TranscriptEntry, ...]:
        """This user's remaining transcript in order; empty if not theirs, refused if gapped."""

    @abstractmethod
    async def status(self, user_id: UserId, call_id: CallId) -> TranscriptStatus:
        """Whether this user's call has a transcript to read; `NOT_RECORDED` if not theirs."""


class TranscriptRetentionRepository(ABC):
    """Deleting expired transcript entries, holding nothing that can decrypt them."""

    @abstractmethod
    async def users_with_entries_at_or_before(
        self, cutoff: datetime, *, after: UserId | None, limit: int
    ) -> list[UserId]:
        """Users with an entry said at or before `cutoff`, in identifier order, after `after`."""

    @abstractmethod
    async def delete_expired(self, user_id: UserId, *, at_or_before: datetime, limit: int) -> int:
        """Delete up to `limit` of this user's entries said by then, counting only its own."""


class SummaryRepository(ABC):
    """The structured record of each call, which outlives its transcript."""

    @abstractmethod
    async def add(self, user_id: UserId, summary: CallSummary) -> None:
        """Write the summary once, raising `RecordNotFoundError` or `AlreadyRecordedError`."""

    @abstractmethod
    async def get(self, user_id: UserId, call_id: CallId) -> CallSummary | None:
        """This user's summary of the call, or nothing."""

    @abstractmethod
    async def for_calls(
        self, user_id: UserId, call_ids: Sequence[CallId]
    ) -> Mapping[CallId, CallSummary]:
        """This user's summaries of these calls, by call, omitting calls with none or not theirs."""


class EscalationContextRepository(ABC):
    """What each user was told about each escalation, so it can be read without a push."""

    @abstractmethod
    async def claim(self, user_id: UserId, context: EscalationContext) -> bool:
        """Store the context unless this call has one, saying whether this was the first."""

    @abstractmethod
    async def get(self, user_id: UserId, call_id: CallId) -> EscalationContext | None:
        """The context, or nothing — which is also what another user's call id gets."""

    @abstractmethod
    async def record_delivery(
        self, user_id: UserId, call_id: CallId, delivery: NotificationDelivery
    ) -> None:
        """Record what became of telling the user."""

    @abstractmethod
    async def mark_ended(self, user_id: UserId, call_id: CallId, at_instant: datetime) -> bool:
        """Record that the call is over, keeping the first end; whether a context was there."""
