"""Calls a handset screened before ringing, reported afterwards: T1, T2 and T3 (D-028)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from tests.e2e.harness import CALLER, Emitted, Pushes, a_user, handset_system, identifying

if TYPE_CHECKING:
    from tests.e2e.app_client import Account, Json
    from tests.e2e.harness import HandsetSystem

pytestmark = pytest.mark.integration

STARTED = datetime(2026, 6, 1, 9, 30, tzinfo=UTC)


def handset_id(serial: str) -> str:
    """An identifier in the handset's own form, a random UUID as `CallScreeningGraph` makes one."""
    return f"00000000-0000-4000-8000-{serial:0>12}"


def report(call: str, event: str, kind: str, *, after_seconds: int = 0, **fields: str) -> Json:
    """One report, as the handset writes it."""
    return {
        "event_id": handset_id(f"e{event}"),
        "call_id": handset_id(call),
        "kind": kind,
        "occurred_at": (STARTED + timedelta(seconds=after_seconds)).isoformat(),
        **fields,
    }


def recorded_as(account: Account, call: str) -> str:
    """How the product names a handset's call: the account's own, and nobody else's."""
    return f"{account.user_id.value}:{handset_id(call)}"


async def reported_twice(system: HandsetSystem, account: Account, *reports: Json) -> None:
    """Each report sent, then the whole batch again, as a handset resends unacknowledged reports."""
    for each in reports:
        receipt = await system.api.report(account, each)
        assert receipt["accepted"] == [each["event_id"]]
    again = await system.api.report(account, *reports)
    assert again["accepted"] == []
    assert again["duplicates"] == [each["event_id"] for each in reports]


async def test_t1_a_call_the_rules_allow_rings_natively_and_is_recorded(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    async with handset_system(database) as system:
        account = await a_user(system)
        call_id = recorded_as(account, "0001")

        await system.api.report(
            account,
            report("0001", "0001", "incoming", caller_number=CALLER, screening="allow"),
        )
        ringing = await system.reaches(account, call_id, CallState.PASSTHROUGH)
        assert ringing.participants == ()
        await system.api.report(account, report("0001", "0002", "answered", after_seconds=4))
        answered = await system.stored_when(
            account, call_id, lambda call: call.has_participant(ParticipantRole.HUMAN)
        )
        assert answered.state is CallState.PASSTHROUGH
        await reported_twice(
            system,
            account,
            report("0001", "0003", "ended", after_seconds=60, ending="completed"),
        )
        detail = await system.ended(account, call_id)

        assert detail["handling"] == "passed_through"
        assert detail["outcome"] == "passed_through"
        assert detail["caller"]["number_withheld"] is False
        [listed] = await system.api.calls(account)
        assert listed["id"] == call_id
        assert listed["status"] == "ended"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        assert [each.role for each in call.participants] == [ParticipantRole.HUMAN]
        assert pushes.sent() == []
        await system.released()
        assert emitted.mentions(*identifying(CALLER)) == []


async def test_t2_a_call_the_rules_reject_never_rings_and_is_recorded_with_why(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    async with handset_system(database) as system:
        account = await a_user(system)
        call_id = recorded_as(account, "0002")

        await reported_twice(
            system,
            account,
            report("0002", "0011", "incoming", caller_number=CALLER, screening="reject"),
            report("0002", "0012", "ended", after_seconds=1, ending="screened_out"),
        )
        detail = await system.ended(account, call_id)

        assert detail["outcome"] == "rejected_by_rule"
        assert detail["handling"] is None
        assert detail["headline"] == "A call from an unknown caller was ended by your rules."
        assert detail["human_joined"] is False
        [listed] = await system.api.calls(account)
        assert listed["outcome"] == "rejected_by_rule"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.REJECTED
        # Nobody picked it up, because it never rang.
        assert call.participants == ()
        assert pushes.sent() == []
        await system.released()
        assert emitted.mentions(*identifying(CALLER)) == []


async def test_t3_a_silenced_call_is_recorded_as_one_that_rang(
    database: str, pushes: Pushes, emitted: Emitted
) -> None:
    async with handset_system(database) as system:
        account = await a_user(system)
        call_id = recorded_as(account, "0003")

        await reported_twice(
            system,
            account,
            report("0003", "0021", "incoming", caller_number=CALLER, screening="silence"),
            report("0003", "0022", "ended", after_seconds=30, ending="missed"),
        )
        detail = await system.ended(account, call_id)

        # A silenced call is recorded as a call allowed to ring.
        assert detail["handling"] == "passed_through"
        assert detail["outcome"] == "passed_through"
        call = await system.stored(account, call_id)
        assert call is not None
        assert call.state is CallState.COMPLETED
        assert call.participants == ()
        await system.released()
        assert emitted.mentions(*identifying(CALLER)) == []
