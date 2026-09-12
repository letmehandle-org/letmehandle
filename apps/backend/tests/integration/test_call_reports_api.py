"""A handset reporting its calls, over HTTP, against a real database.

Four properties: nobody reports without signing in; a handset speaks only for its own account;
a report sent twice counts once; and what it reports becomes the call events every transport
produces.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from letmehandle.adapters.database.models import CallReportRow
from letmehandle.adapters.transport.android_native.transport import AndroidNativeCallTransport
from letmehandle.api.call_report_schemas import MAX_REPORTS_PER_REQUEST
from letmehandle.api.call_reports import REPORTS_BODY_LIMIT_BYTES
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
    # Said to be absent out loud, as a handset serialising an empty field does.
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
        "bad",
        [
            reported("event-0001", "participant_joined"),
            reported("event-0001", "answered", screening="allow"),
            reported("event-0001", "ended"),
            reported("event-0001", "incoming", ending="missed"),
            reported("event-0001", "incoming", caller_number="2025550145"),
            {**reported("event-0001", "incoming"), "occurred_at": "2026-09-13T12:00:00"},
            reported("x", "incoming"),
        ],
    )
    async def test_what_cannot_have_happened_is_refused(
        self, api: Api, bad: dict[str, Any]
    ) -> None:
        tokens = await sign_in(api)
        response = await send(api, tokens, [bad])
        assert response.status_code == 422, response.text
        assert await drain(api) == []

    async def test_an_empty_batch_is_refused(self, api: Api) -> None:
        tokens = await sign_in(api)
        assert (await send(api, tokens, [])).status_code == 422


class TestSize:
    async def test_a_body_larger_than_a_full_batch_is_refused_before_it_is_read(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        size = REPORTS_BODY_LIMIT_BYTES + 1

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
        # The second account's handset reuses the first account's identifiers, as a handset
        # guessing them would. It creates its own call; it does not touch the first one.
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
