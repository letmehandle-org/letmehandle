"""Deleting an account, over HTTP, against a real database: nothing of the person is left.

The residue check reads every table the schema declares rather than a list written here, so a
table added later is covered without anybody remembering this test. It also insists that every
table held something of the person before, so a new table nobody seeds here fails loudly instead
of passing because it was empty.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlEscalationContextRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.models import Base
from letmehandle.adapters.database.repositories import (
    SqlOnboardingRepository,
    SqlPreferencesRepository,
    SqlUserRepository,
)
from letmehandle.adapters.database.session import unit_of_work
from letmehandle.application.calls.fallback import CallFacts, fallback_summary
from letmehandle.domain.models.call import (
    CallHandling,
    CallSession,
    Participant,
    ParticipantRole,
    Speaker,
    TranscriptEntry,
)
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationContext
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.onboarding import OnboardingProgress, OnboardingStep
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import UserPreferences
from tests.contracts.fakes import FixedClock
from tests.integration.conftest import ANOTHER_NUMBER, NUMBER, bearer, sign_in

if TYPE_CHECKING:
    from typing import Any

    from letmehandle.domain.ports.security import TranscriptCipher
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Account:
    """A signed-in person, and every value a row about them could be recognised by."""

    user_id: UserId
    number: str
    call_id: str
    report_call_id: str
    tokens: dict[str, Any]

    @property
    def marks(self) -> tuple[str, ...]:
        return (self.user_id.value, self.number, self.call_id, self.report_call_id)


async def an_account(api: Api, number: str) -> Account:
    """Sign in, and leave something of the person in every table there is."""
    tokens = await sign_in(api, number)
    me = await api.client.get("/v1/me", headers=bearer(tokens))
    user_id = UserId(me.json()["id"])
    account = Account(
        user_id=user_id,
        number=number,
        call_id=f"call-{uuid.uuid4()}",
        report_call_id=str(uuid.uuid4()),
        tokens=tokens,
    )
    device = await api.client.put(
        "/v1/devices",
        headers=bearer(tokens),
        json={"platform": "android", "token": f"device-{uuid.uuid4()}"},
    )
    assert device.status_code == 204, device.text
    report = await api.client.post(
        "/v1/calls/reports",
        headers=bearer(tokens),
        json={
            "reports": [
                {
                    "event_id": str(uuid.uuid4()),
                    "call_id": account.report_call_id,
                    "kind": "incoming",
                    "occurred_at": NOW.isoformat(),
                }
            ]
        },
    )
    assert report.status_code in {200, 202}, report.text
    await _stored_call(api, account)
    return account


async def _stored_call(api: Api, account: Account) -> None:
    clock = FixedClock(NOW)
    cipher: TranscriptCipher = api.app.state.container.transcript_cipher
    call = CallSession.restore(
        id=CallId(account.call_id),
        user_id=account.user_id,
        caller=Caller(number=PhoneNumber("+12025550187")),
        started_at=NOW,
        state=CallState.COMPLETED,
        participants=(Participant(ParticipantRole.CALLER, NOW, NOW + timedelta(seconds=60)),),
        ended_at=NOW + timedelta(seconds=60),
        handling=CallHandling.ASSISTANT,
    )
    async with unit_of_work(api.app.state.session_factory) as session:
        await SqlPreferencesRepository(session, clock).save(account.user_id, UserPreferences())
        await SqlOnboardingRepository(session, clock).save(
            account.user_id, OnboardingProgress(completed=frozenset({OnboardingStep.HOURS}))
        )
        await SqlCallRepository(session, cipher, clock).save(call)
        await SqlTranscriptRepository(session, cipher).append(
            account.user_id, call.id, [TranscriptEntry(Speaker.CALLER, "Hello there.", NOW)]
        )
        await SqlSummaryRepository(session, cipher, clock).add(
            account.user_id, fallback_summary(CallFacts(call), locale="en")
        )
        await SqlEscalationContextRepository(session, cipher).claim(
            account.user_id,
            EscalationContext(
                call_id=call.id,
                reason=EscalationReason.CALLER_ASKED_FOR_THE_USER,
                raised_at=NOW,
                needed="A word with them.",
            ),
        )


async def rows_about(api: Api, account: Account) -> dict[str, int]:
    """For every table the schema has, how many rows mention the account in any column."""
    counts: dict[str, int] = {}
    async with unit_of_work(api.app.state.session_factory) as session:
        for table in Base.metadata.sorted_tables:
            result = await session.execute(
                text(f'SELECT t::text FROM "{table.name}" t')  # noqa: S608 - names from the schema
            )
            counts[table.name] = sum(
                1 for row in result.scalars() if any(mark in row for mark in account.marks)
            )
    return counts


class TestDeletingAnAccount:
    async def test_nothing_of_the_person_is_left_in_any_table_and_nobody_else_is_touched(
        self, api: Api
    ) -> None:
        leaving = await an_account(api, NUMBER)
        staying = await an_account(api, ANOTHER_NUMBER)
        before = await rows_about(api, leaving)
        assert [table for table, count in before.items() if count == 0] == []

        response = await api.client.delete("/v1/me", headers=bearer(leaving.tokens))

        assert response.status_code == 204
        after = await rows_about(api, leaving)
        assert [table for table, count in after.items() if count] == []
        kept = await rows_about(api, staying)
        assert [table for table, count in kept.items() if count == 0] == []

    async def test_the_account_s_tokens_stop_working(self, api: Api) -> None:
        leaving = await an_account(api, NUMBER)
        await api.client.delete("/v1/me", headers=bearer(leaving.tokens))

        assert (await api.client.get("/v1/me", headers=bearer(leaving.tokens))).status_code == 401
        renewed = await api.client.post(
            "/v1/auth/refresh", json={"refresh_token": leaving.tokens["refresh_token"]}
        )
        assert renewed.status_code == 401

    async def test_a_live_call_is_ended_before_anything_is_removed(self, api: Api) -> None:
        leaving = await an_account(api, NUMBER)
        calls = RecordingCalls(api)
        api.app.state.orchestrator = calls

        response = await api.client.delete("/v1/me", headers=bearer(leaving.tokens))

        assert response.status_code == 204
        assert calls.ended == [(leaving.user_id, True)]

    async def test_it_needs_a_token(self, api: Api) -> None:
        response = await api.client.delete("/v1/me")
        assert response.status_code == 401


class RecordingCalls:
    """Stands in for the orchestrator, noting whether the account was still stored when asked."""

    def __init__(self, api: Api) -> None:
        self._api = api
        self.ended: list[tuple[UserId, bool]] = []

    async def end_calls_of(self, user_id: UserId) -> None:
        async with unit_of_work(self._api.app.state.session_factory) as session:
            stored = await SqlUserRepository(session, FixedClock(NOW)).get(user_id)
        self.ended.append((user_id, stored is not None))
