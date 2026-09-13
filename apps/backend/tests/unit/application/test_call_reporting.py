"""Accepting a handset's reports: scoped to the user, inert when repeated, ordered by state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.application.calls.reports import (
    CallReporting,
    ReportedCallOwnership,
    ReportingPolicy,
    ReportingRateLimitedError,
    scoped_call_id,
)
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    ScreeningDecision,
)
from letmehandle.domain.ports.reported_calls import CallEnding, CallReport
from tests.contracts.auth_fakes import CountingRateLimiter
from tests.contracts.call_report_fakes import (
    InMemoryCallReportRepository,
    RecordingCallEventSink,
)

USER = UserId("user-1")
SOMEBODY_ELSE = UserId("user-2")
CALLER = PhoneNumber.parse("+12025550143")
AT = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def report(
    event: str,
    kind: CallEventKind,
    *,
    call: str = "call-1",
    screening: ScreeningDecision | None = None,
    ending: CallEnding | None = None,
    caller: PhoneNumber | None = None,
) -> CallReport:
    return CallReport(
        event_id=EventId(event),
        call_id=CallId(call),
        kind=kind,
        occurred_at=AT,
        caller_number=caller,
        screening=screening,
        ending=ending,
    )


@pytest.fixture
def repository() -> InMemoryCallReportRepository:
    return InMemoryCallReportRepository()


@pytest.fixture
def sink() -> RecordingCallEventSink:
    return RecordingCallEventSink()


@pytest.fixture
def reporting(
    repository: InMemoryCallReportRepository, sink: RecordingCallEventSink
) -> CallReporting:
    return CallReporting(repository, sink, CountingRateLimiter())


class TestRateLimit:
    async def test_a_handset_reporting_too_often_is_told_when_to_try_again(
        self, repository: InMemoryCallReportRepository, sink: RecordingCallEventSink
    ) -> None:
        reporting = CallReporting(
            repository,
            sink,
            CountingRateLimiter(),
            ReportingPolicy(requests_per_window=2, window=timedelta(minutes=1)),
        )
        for number in range(2):
            await reporting.report(USER, [report(f"e{number}", CallEventKind.INCOMING)])

        with pytest.raises(ReportingRateLimitedError) as refused:
            await reporting.report(USER, [report("e9", CallEventKind.INCOMING)])

        assert refused.value.retry_after_seconds == 60
        # Refused before anything was stored or handed on.
        assert (USER, "e9") not in repository.rows
        assert len(sink.published) == 2
        # Counted per user: another account's handset is not held back by this one.
        await reporting.report(SOMEBODY_ELSE, [report("e9", CallEventKind.INCOMING)])


class TestMapping:
    async def test_a_screened_call_becomes_the_same_events_the_streaming_transport_produces(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        await reporting.report(
            USER,
            [
                report(
                    "e1", CallEventKind.INCOMING, screening=ScreeningDecision.SILENCE, caller=CALLER
                ),
                report("e2", CallEventKind.ANSWERED),
                report("e3", CallEventKind.ENDED, ending=CallEnding.COMPLETED),
            ],
        )

        assert [event.kind for event in sink.published] == [
            CallEventKind.INCOMING,
            CallEventKind.ANSWERED,
            CallEventKind.ENDED,
        ]
        incoming, _, ended = sink.published
        assert incoming.screening is ScreeningDecision.SILENCE
        assert incoming.caller is not None
        assert incoming.caller.number == CALLER
        assert ended.detail == "completed"
        assert ended.caller is None

    async def test_each_event_says_when_the_handset_saw_it_happen(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        # Not when the report arrived: a handset offline for an hour reports an hour late.
        rang = CallReport(
            event_id=EventId("e1"),
            call_id=CallId("call-1"),
            kind=CallEventKind.INCOMING,
            occurred_at=AT - timedelta(hours=1),
        )
        await reporting.report(USER, [rang])

        assert [event.occurred_at for event in sink.published] == [AT - timedelta(hours=1)]

    async def test_identifiers_name_the_user_as_well_as_the_handset_call(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        # Otherwise two accounts whose handsets chose the same identifier would be one call.
        await reporting.report(USER, [report("e1", CallEventKind.INCOMING)])
        await reporting.report(SOMEBODY_ELSE, [report("e1", CallEventKind.INCOMING)])

        first, second = sink.published
        assert first.call_id == scoped_call_id(USER, CallId("call-1"))
        assert first.call_id != second.call_id
        assert first.event_id != second.event_id


class TestIdempotency:
    async def test_a_resent_report_is_acknowledged_and_not_handed_on_again(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        batch = [report("e1", CallEventKind.INCOMING)]
        first = await reporting.report(USER, batch)
        again = await reporting.report(USER, batch)

        assert first.accepted == (EventId("e1"),)
        assert again.accepted == ()
        assert again.duplicates == (EventId("e1"),)
        assert len(sink.published) == 1

    async def test_a_repeat_within_one_batch_counts_once(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        outcome = await reporting.report(
            USER, [report("e1", CallEventKind.INCOMING), report("e1", CallEventKind.INCOMING)]
        )
        assert outcome.accepted == (EventId("e1"),)
        assert outcome.duplicates == (EventId("e1"),)
        assert len(sink.published) == 1

    async def test_the_same_event_identifier_from_another_user_is_not_a_repeat(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        await reporting.report(USER, [report("e1", CallEventKind.INCOMING)])
        outcome = await reporting.report(SOMEBODY_ELSE, [report("e1", CallEventKind.INCOMING)])
        assert outcome.accepted == (EventId("e1"),)
        assert len(sink.published) == 2


class TestOrdering:
    async def test_a_report_arriving_after_the_call_ended_is_stored_but_not_replayed(
        self,
        reporting: CallReporting,
        repository: InMemoryCallReportRepository,
        sink: RecordingCallEventSink,
    ) -> None:
        await reporting.report(
            USER,
            [
                report("e1", CallEventKind.INCOMING),
                report("e3", CallEventKind.ENDED, ending=CallEnding.MISSED),
            ],
        )
        outcome = await reporting.report(USER, [report("e2", CallEventKind.ANSWERED)])

        assert outcome.accepted == (EventId("e2"),)
        assert (USER, "e2") in repository.rows
        assert [event.kind for event in sink.published] == [
            CallEventKind.INCOMING,
            CallEventKind.ENDED,
        ]

    async def test_another_users_ended_call_does_not_suppress_this_users_call(
        self, reporting: CallReporting, sink: RecordingCallEventSink
    ) -> None:
        await reporting.report(
            SOMEBODY_ELSE, [report("x1", CallEventKind.ENDED, ending=CallEnding.MISSED)]
        )
        await reporting.report(USER, [report("e1", CallEventKind.INCOMING)])
        assert sink.published[-1].kind is CallEventKind.INCOMING


class TestWhatAHandsetCanReport:
    @pytest.mark.parametrize(
        "kind",
        [CallEventKind.PARTICIPANT_JOINED, CallEventKind.PARTICIPANT_LEFT, CallEventKind.FAILED],
    )
    def test_what_a_handset_cannot_observe_is_refused(self, kind: CallEventKind) -> None:
        with pytest.raises(InvariantError, match="cannot observe"):
            report("e1", kind)

    def test_a_moment_without_a_zone_is_refused(self) -> None:
        with pytest.raises(InvariantError, match="timezone"):
            CallReport(
                EventId("e1"),
                CallId("c1"),
                CallEventKind.INCOMING,
                occurred_at=AT.replace(tzinfo=None),
            )

    def test_a_screening_decision_belongs_to_the_incoming_event(self) -> None:
        with pytest.raises(InvariantError, match="incoming event only"):
            report("e1", CallEventKind.ANSWERED, screening=ScreeningDecision.ALLOW)

    def test_only_an_ended_call_says_how_it_ended(self) -> None:
        with pytest.raises(InvariantError, match="how it ended"):
            report("e1", CallEventKind.ENDED)
        with pytest.raises(InvariantError, match="how it ended"):
            report("e1", CallEventKind.INCOMING, ending=CallEnding.MISSED)


class TestOwnership:
    async def test_a_reported_call_belongs_to_the_account_that_reported_it(self) -> None:
        event = CallEvent(
            CallEventKind.INCOMING, scoped_call_id(USER, CallId("call-1")), EventId("e")
        )
        assert await ReportedCallOwnership().owner_of(event) == USER

    @pytest.mark.parametrize("call", ["unscoped", ":no-user"])
    async def test_a_call_no_account_reported_belongs_to_nobody(self, call: str) -> None:
        event = CallEvent(CallEventKind.INCOMING, CallId(call), EventId("e"))
        assert await ReportedCallOwnership().owner_of(event) is None
