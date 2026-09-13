"""A user's calls, read back afterwards: listed, opened, read in full, and deleted.

Every method takes the user whose calls they are, and passes it to every repository it reads,
so a call belonging to somebody else is not found rather than refused — the answer for another
user's call and for no call at all is the same answer, and gives nothing away (D-012).

What a user is shown about who called is decided here rather than in the HTTP layer, because it
is a rule and not a format: the category always, a name only for a caller the user has told us
about, and the number never. A stranger's number is somebody else's personal data, the user's
phone already showed it when it rang, and a history that repeats it is one more place it can
leak from (D-014, D-021).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from letmehandle.application.calls.participation import answered_at, human_joined_at
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.ports.repositories import TranscriptStatus

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.models.call import CallSession, TranscriptEntry
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.repositories import (
        CallCursor,
        CallFilter,
        CallRepository,
        PreferencesRepository,
        SummaryRepository,
        TranscriptRepository,
    )


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One call as history shows it: the call, and its summary once it has one.

    A call with no summary is still a record. It is either still going, or it ended and its
    summary has not been written yet; either way it rang the user's phone, and a history that
    left it out would be missing a call they know they had — one they could not delete, either.
    """

    call: CallSession
    summary: CallSummary | None

    @property
    def caller(self) -> Caller:
        """Who the caller was taken to be: the summary's view once there is one."""
        return self.call.caller if self.summary is None else self.summary.caller

    @property
    def display_name(self) -> str | None:
        """A name to show, only for somebody the user knows; a network can name a stranger."""
        caller = self.caller
        return caller.display_name if caller.is_known else None

    @property
    def human_joined_at(self) -> datetime | None:
        if self.summary is not None:
            return self.summary.human_joined_at
        return human_joined_at(self.call)

    @property
    def answered_at(self) -> datetime | None:
        return answered_at(self.call)

    @property
    def duration_seconds(self) -> float | None:
        """How long it lasted, or None while it is still going."""
        return self.call.duration_seconds()


@dataclass(frozen=True, slots=True)
class RecordPage:
    """Some of a user's call records, newest first, and where the next page starts."""

    records: tuple[CallRecord, ...]
    next_cursor: CallCursor | None


@dataclass(frozen=True, slots=True)
class Retention:
    """How long a call's transcript is kept, as the user's setting stands today.

    Today's setting rather than the one in force when the call was made, because that is the
    setting the purge applies (see the retention module): a user who shortens it sees their older
    transcripts go now, and history has to say what will actually happen.
    """

    days: int

    def expires_at(self, call: CallSession) -> datetime | None:
        """When the last of the call's transcript becomes due for deletion, once it has ended.

        Every line expires a retention after it was said, and the last line was said by the end
        of the call, so this is when nothing of it will be left once the purge has run.
        """
        return None if call.ended_at is None else call.ended_at + timedelta(days=self.days)


@dataclass(frozen=True, slots=True)
class CallDetail:
    """A record opened in full: whether its transcript can be read, and for how long."""

    record: CallRecord
    transcript: TranscriptStatus
    retention: Retention

    @property
    def transcript_expires_at(self) -> datetime | None:
        if self.transcript is not TranscriptStatus.RETAINED:
            return None
        return self.retention.expires_at(self.record.call)


@dataclass(frozen=True, slots=True)
class TranscriptView:
    """What can be read of a call's transcript, and why nothing can when nothing can.

    `entries` is empty unless `status` is `RETAINED`. It can be empty then too, for the moment
    between the purge taking a call's last line and this read — which is the truth: nothing is
    left.
    """

    call: CallSession
    status: TranscriptStatus
    entries: tuple[TranscriptEntry, ...]
    retention: Retention


class CallHistoryService:
    """Listing, opening, reading and deleting one user's calls."""

    def __init__(
        self,
        *,
        calls: CallRepository,
        summaries: SummaryRepository,
        transcripts: TranscriptRepository,
        preferences: PreferencesRepository,
    ) -> None:
        self._calls = calls
        self._summaries = summaries
        self._transcripts = transcripts
        self._preferences = preferences

    async def page(
        self,
        user_id: UserId,
        *,
        limit: int,
        after: CallCursor | None,
        matching: CallFilter,
    ) -> RecordPage:
        """A page of this user's call records, newest first, holding only what matches."""
        page = await self._calls.list_for_user(user_id, limit=limit, after=after, matching=matching)
        summaries = await self._summaries.for_calls(user_id, [call.id for call in page.calls])
        return RecordPage(
            records=tuple(CallRecord(call, summaries.get(call.id)) for call in page.calls),
            next_cursor=page.next_cursor,
        )

    async def detail(self, user_id: UserId, call_id: CallId) -> CallDetail | None:
        """This user's call in full, or None — which is also the answer for somebody else's."""
        call = await self._calls.get(user_id, call_id)
        if call is None:
            return None
        return CallDetail(
            record=CallRecord(call, await self._summaries.get(user_id, call_id)),
            transcript=await self._transcripts.status(user_id, call_id),
            retention=await self._retention(user_id),
        )

    async def transcript(self, user_id: UserId, call_id: CallId) -> TranscriptView | None:
        """What remains of this user's call's transcript, or None if there is no such call."""
        call = await self._calls.get(user_id, call_id)
        if call is None:
            return None
        status = await self._transcripts.status(user_id, call_id)
        entries = (
            await self._transcripts.for_call(user_id, call_id)
            if status is TranscriptStatus.RETAINED
            else ()
        )
        return TranscriptView(
            call=call, status=status, entries=entries, retention=await self._retention(user_id)
        )

    async def delete(self, user_id: UserId, call_id: CallId) -> None:
        """Delete the call and everything recorded about it. Nothing there is not an error."""
        await self._calls.delete(user_id, call_id)

    async def _retention(self, user_id: UserId) -> Retention:
        stored = await self._preferences.get(user_id)
        return Retention((stored or UserPreferences()).transcript_retention_days)
