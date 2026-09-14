"""A handset reporting its calls over HTTP: signed in, its own account only, once each."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select, text

from letmehandle.adapters.database.models import CallReportRow
from letmehandle.adapters.transport.android_native.transport import AndroidNativeCallTransport
from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES
from letmehandle.api.call_report_schemas import MAX_REPORTS_PER_REQUEST
from letmehandle.application.calls.reports import ReportingPolicy
from letmehandle.domain.ports.call_transport import CallEventKind, ScreeningDecision
from tests.integration.conftest import ANOTHER_NUMBER, bearer, sign_in

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from letmehandle.domain.ports.call_transport import CallEvent
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

CALL = "7d1c9a52-0b7e-4c56-9d4f-2f1f0f6f7a01"
CALLER = "+12025550145"


def reported(event_id: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "call_id": CALL,
        "kind": kind,
        "occurred_at": "2026-09-13T12:00:00+01:00",
        **extra,
    }


SCREENED_CALL = [
    reported("event-0001", "incoming", screening="silence", caller_number=CALLER),
    # Absent, written out as null.
    reported("event-0002", "answered", caller_number=None),
    reported("event-0003", "ended", ending="completed"),
]


async def send(api: Api, tokens: dict[str, Any], reports: list[dict[str, Any]]) -> Any:
    return await api.client.post(
        "/v1/calls/reports", headers=bearer(tokens), json={"reports": reports}
    )


async def drain(api: Api) -> list[CallEvent]:
    """Everything handed on so far. A stream per read: timing one out closes it."""
    transport = api.app.state.container.reported_calls
    assert isinstance(transport, AndroidNativeCallTransport)
    events: list[CallEvent] = []
    while True:
        try:
            events.append(await asyncio.wait_for(anext(transport.events()), timeout=0.05))
        except TimeoutError:
            return events


class TestAuthentication:
    async def test_reporting_needs_a_signed_in_user(self, api: Api) -> None:
        response = await api.client.post(
            "/v1/calls/reports", json={"reports": [reported("event-0001", "incoming")]}
        )
        assert response.status_code == 401


class TestMapping:
    async def test_a_screened_call_becomes_incoming_answered_and_ended(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await send(api, tokens, SCREENED_CALL)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "accepted": ["event-0001", "event-0002", "event-0003"],
            "duplicates": [],
            "rejected": [],
        }
        events = await drain(api)
        assert [event.kind for event in events] == [
            CallEventKind.INCOMING,
            CallEventKind.ANSWERED,
            CallEventKind.ENDED,
        ]
        assert events[0].screening is ScreeningDecision.SILENCE
        caller = events[0].caller
        assert caller is not None
        assert caller.number is not None
        assert caller.number.value == CALLER
        assert events[2].detail == "completed"
        me = (await api.client.get("/v1/me", headers=bearer(tokens))).json()
        assert {event.call_id.value for event in events} == {f"{me['id']}:{CALL}"}

    @pytest.mark.parametrize(
        ("bad", "field"),
        [
            (reported("event-0001", "participant_joined"), "kind"),
            (reported("event-0001", "answered", screening="allow"), "screening"),
            (reported("event-0001", "ended"), "ended"),
            (reported("event-0001", "incoming", ending="missed"), "ended"),
            (reported("event-0001", "incoming", caller_number="2025550145"), "caller_number"),
            (reported("event-0001", "incoming", caller_number="+0555"), "caller_number"),
            # Digits no telephone network routes.
            (
                reported("event-0001", "incoming", caller_number="+1\u0662\u0660\u0662"),
                "caller_number",
            ),
            (
                reported("event-0001", "incoming", caller_number="+1202555014500000"),
                "caller_number",
            ),
            (
                {**reported("event-0001", "incoming"), "occurred_at": "2026-09-13T12:00:00"},
                "occurred_at",
            ),
            (reported("event-0001", "incoming", surprise=True), "surprise"),
        ],
    )
    async def test_what_cannot_have_happened_is_rejected_by_its_event_id(
        self, api: Api, bad: dict[str, Any], field: str
    ) -> None:
        tokens = await sign_in(api)
        response = await send(api, tokens, [bad])
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["accepted"], body["duplicates"]) == ([], [])
        [rejected] = body["rejected"]
        assert (rejected["index"], rejected["event_id"]) == (0, "event-0001")
        assert field in rejected["reason"]
        assert await drain(api) == []

    @pytest.mark.parametrize("event_id", ["x", 7, None])
    async def test_a_report_whose_event_id_is_unusable_is_rejected_by_its_place(
        self, api: Api, event_id: object
    ) -> None:
        tokens = await sign_in(api)
        bad = {**reported("event-0001", "incoming"), "event_id": event_id}
        body = (await send(api, tokens, [SCREENED_CALL[0], bad])).json()
        assert body["accepted"] == ["event-0001"]
        assert [(each["index"], each["event_id"]) for each in body["rejected"]] == [(1, None)]

    async def test_something_that_is_not_a_report_at_all_is_rejected_by_its_place(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        body = (await send(api, tokens, ["not a report", SCREENED_CALL[0]])).json()  # type: ignore[list-item]
        assert body["accepted"] == ["event-0001"]
        assert [(each["index"], each["event_id"]) for each in body["rejected"]] == [(0, None)]

    async def test_one_invalid_report_does_not_hold_back_the_rest_of_its_batch(
        self, api: Api
    ) -> None:
        # One unreadable report does not refuse the batch.
        tokens = await sign_in(api)
        invalid = reported("event-0009", "incoming", caller_number="12025550145")
        response = await send(api, tokens, [SCREENED_CALL[0], invalid, *SCREENED_CALL[1:]])
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"] == ["event-0001", "event-0002", "event-0003"]
        assert [(each["index"], each["event_id"]) for each in body["rejected"]] == [
            (1, "event-0009")
        ]
        assert len(await drain(api)) == 3

    async def test_a_short_number_that_is_still_e164_is_accepted(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await send(
            api, tokens, [reported("event-0001", "incoming", caller_number="+123")]
        )
        assert response.json()["accepted"] == ["event-0001"]

    async def test_a_batch_that_is_not_a_batch_is_refused_whole(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/calls/reports", headers=bearer(tokens), json={"reports": "all of them"}
        )
        assert response.status_code == 422

    async def test_an_empty_batch_is_refused(self, api: Api) -> None:
        tokens = await sign_in(api)
        assert (await send(api, tokens, [])).status_code == 422


class TestSize:
    async def test_a_body_larger_than_a_full_batch_is_refused_before_it_is_read(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        size = JSON_BODY_LIMIT_BYTES + 1

        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(size // 4_096 + 1):
                yield b" " * 4_096

        headers = {**bearer(tokens), "Content-Type": "application/json"}
        declared = await api.client.post("/v1/calls/reports", headers=headers, content=b" " * size)
        streamed = await api.client.post("/v1/calls/reports", headers=headers, content=chunks())
        assert (declared.status_code, streamed.status_code) == (413, 413)
        assert declared.json()["error"] == "payload_too_large"

    async def test_a_full_batch_fits(self, api: Api) -> None:
        tokens = await sign_in(api)
        batch = [
            reported(
                f"event-{index:04d}-7d1c9a52-0b7e-4c56-9d4f-2f1f0f6f",
                "incoming",
                screening="silence",
                caller_number=CALLER,
            )
            for index in range(MAX_REPORTS_PER_REQUEST)
        ]
        response = await send(api, tokens, batch)
        assert response.status_code == 200, response.text


class TestRateLimit:
    async def test_a_handset_reporting_too_often_is_refused_with_when_to_try_again(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        allowed = ReportingPolicy().requests_per_window
        for _ in range(allowed):
            assert (await send(api, tokens, [SCREENED_CALL[0]])).status_code == 200

        refused = await send(api, tokens, [SCREENED_CALL[0]])

        assert refused.status_code == 429
        assert refused.json()["error"] == "rate_limited"
        assert int(refused.headers["Retry-After"]) >= 1


class TestIdempotency:
    async def test_a_resent_batch_is_acknowledged_and_counted_once(
        self, api: Api, session: AsyncSession
    ) -> None:
        tokens = await sign_in(api)
        await send(api, tokens, SCREENED_CALL)
        await drain(api)

        again = await send(api, tokens, SCREENED_CALL)

        assert again.status_code == 200
        assert again.json() == {
            "accepted": [],
            "duplicates": ["event-0001", "event-0002", "event-0003"],
            "rejected": [],
        }
        assert await drain(api) == []
        stored = await session.execute(select(func.count()).select_from(CallReportRow))
        assert stored.scalar_one() == 3

    async def test_a_late_report_after_the_call_ended_is_stored_and_not_replayed(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        await send(api, tokens, [SCREENED_CALL[0], SCREENED_CALL[2]])
        await drain(api)

        late = await send(api, tokens, [SCREENED_CALL[1]])

        assert late.json()["accepted"] == ["event-0002"]
        assert await drain(api) == []


class TestIsolation:
    async def test_a_handset_speaks_only_for_its_own_account(
        self, api: Api, session: AsyncSession
    ) -> None:
        # The second account's handset reuses the first account's identifiers.
        mine = await sign_in(api)
        theirs = await sign_in(api, ANOTHER_NUMBER)
        await send(api, mine, [SCREENED_CALL[0], SCREENED_CALL[2]])
        first_calls = {event.call_id for event in await drain(api)}

        response = await send(api, theirs, SCREENED_CALL)

        assert response.json()["accepted"] == ["event-0001", "event-0002", "event-0003"]
        their_events = await drain(api)
        # Not suppressed by the first account's ended call, and not the same call.
        assert [event.kind for event in their_events] == [
            CallEventKind.INCOMING,
            CallEventKind.ANSWERED,
            CallEventKind.ENDED,
        ]
        assert first_calls.isdisjoint({event.call_id for event in their_events})
        owners = await session.execute(
            select(CallReportRow.user_id, func.count()).group_by(CallReportRow.user_id)
        )
        assert sorted(count for _, count in owners.all()) == [2, 3]


class TestStorage:
    async def test_the_caller_s_number_is_not_kept_in_clear(
        self, api: Api, session: AsyncSession
    ) -> None:
        # A call's caller is sealed, so no report stores the number in clear (D-014).
        tokens = await sign_in(api)
        await send(api, tokens, SCREENED_CALL)
        assert len(await drain(api)) == len(SCREENED_CALL)

        rows = (await session.execute(text("SELECT r::text FROM call_reports r"))).scalars().all()

        assert len(rows) == len(SCREENED_CALL)
        assert [row for row in rows if CALLER.removeprefix("+") in row] == []
