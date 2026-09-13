"""Escalation contexts, against a real database: sealing, deduplication, isolation, and ending."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, text

from letmehandle.adapters.database.call_repositories import SqlEscalationContextRepository
from letmehandle.adapters.database.models import UserRow
from letmehandle.adapters.database.repositories import SqlUserRepository
from letmehandle.adapters.security.transcript_cipher import AesGcmTranscriptCipher
from letmehandle.domain.errors import DecryptionError
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User
from tests.contracts.fakes import FixedClock
from tests.support.stored_bytes import assert_nowhere_in

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

RAISED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ALICE = UserId("user-1")
BOB = UserId("user-2")
CALL = CallId("call-1")
KEY = ("key-a", bytes(range(32)))


def a_context(**overrides: object) -> EscalationContext:
    values: dict[str, object] = {
        "call_id": CALL,
        "reason": EscalationReason.CALLER_ASKED_FOR_THE_USER,
        "raised_at": RAISED,
        "caller_label": "a courier",
        "established": "They are at the gate.",
        "needed": "Where to leave the parcel.",
    }
    values.update(overrides)
    return EscalationContext(**values)  # type: ignore[arg-type]


@pytest.fixture
async def contexts(session: AsyncSession) -> SqlEscalationContextRepository:
    users = SqlUserRepository(session, FixedClock(RAISED))
    await users.add(User(id=ALICE, phone_number=PhoneNumber.parse("+12025550143")))
    await users.add(User(id=BOB, phone_number=PhoneNumber.parse("+12025550144")))
    return SqlEscalationContextRepository(session, AesGcmTranscriptCipher([KEY]))


async def test_a_context_round_trips(contexts: SqlEscalationContextRepository) -> None:
    assert await contexts.claim(ALICE, a_context())
    assert await contexts.get(ALICE, CALL) == a_context()


async def test_a_minimal_context_round_trips(contexts: SqlEscalationContextRepository) -> None:
    sparse = a_context(caller_label=None, established=None, needed=None)
    await contexts.claim(ALICE, sparse)
    assert await contexts.get(ALICE, CALL) == sparse


async def test_nothing_the_user_was_told_is_stored_in_clear(
    session: AsyncSession, contexts: SqlEscalationContextRepository
) -> None:
    # The two sentences are the model's account of what the caller said (D-014).
    await contexts.claim(ALICE, a_context())
    await session.flush()
    for words in ("a courier", "They are at the gate.", "Where to leave the parcel."):
        await assert_nowhere_in(session, "escalation_contexts", 1, words)


async def test_a_context_with_nothing_to_say_stores_nothing_sealed(
    session: AsyncSession, contexts: SqlEscalationContextRepository
) -> None:
    await contexts.claim(ALICE, a_context(caller_label=None, established=None, needed=None))
    stored = await session.execute(text("SELECT key_id, ciphertext FROM escalation_contexts"))
    assert stored.one() == (None, None)


@pytest.mark.parametrize(
    "tampering",
    [
        "UPDATE escalation_contexts SET user_id = 'user-2'",
        "UPDATE escalation_contexts SET call_id = 'call-2'",
        "UPDATE escalation_contexts SET reason = 'decision_needs_the_user'",
        "UPDATE escalation_contexts SET raised_at = raised_at - interval '1 hour'",
    ],
    ids=["another-user", "another-call", "another-reason", "another-moment"],
)
async def test_sealed_words_moved_onto_another_escalation_do_not_open(
    session: AsyncSession, contexts: SqlEscalationContextRepository, tampering: str
) -> None:
    await contexts.claim(ALICE, a_context())
    await session.execute(text(tampering))
    session.expunge_all()
    rows = await session.execute(text("SELECT user_id, call_id FROM escalation_contexts"))
    user, call = rows.one()
    with pytest.raises(DecryptionError):
        await contexts.get(UserId(user), CallId(call))


async def test_the_first_claim_wins_and_a_repeat_changes_nothing(
    contexts: SqlEscalationContextRepository,
) -> None:
    assert await contexts.claim(ALICE, a_context())
    assert not await contexts.claim(ALICE, a_context(needed="Something else entirely."))
    stored = await contexts.get(ALICE, CALL)
    assert stored is not None
    assert stored.needed == "Where to leave the parcel."


async def test_one_user_cannot_read_or_block_another_s_context(
    contexts: SqlEscalationContextRepository,
) -> None:
    await contexts.claim(ALICE, a_context())
    assert await contexts.get(BOB, CALL) is None
    # The same call id for a different user is a different escalation.
    assert await contexts.claim(BOB, a_context(needed="Bob's own."))
    bobs = await contexts.get(BOB, CALL)
    assert bobs is not None
    assert bobs.needed == "Bob's own."


async def test_delivery_is_recorded_for_that_user_only(
    contexts: SqlEscalationContextRepository,
) -> None:
    await contexts.claim(ALICE, a_context())
    await contexts.claim(BOB, a_context())
    await contexts.record_delivery(ALICE, CALL, NotificationDelivery.FAILED)

    alices, bobs = await contexts.get(ALICE, CALL), await contexts.get(BOB, CALL)
    assert alices is not None and alices.delivery is NotificationDelivery.FAILED
    assert bobs is not None and bobs.delivery is NotificationDelivery.PENDING


async def test_ending_marks_it_over_and_keeps_the_first_end(
    contexts: SqlEscalationContextRepository,
) -> None:
    await contexts.claim(ALICE, a_context())
    first = RAISED + timedelta(minutes=3)
    assert await contexts.mark_ended(ALICE, CALL, first)
    assert await contexts.mark_ended(ALICE, CALL, first + timedelta(minutes=1))

    stored = await contexts.get(ALICE, CALL)
    assert stored is not None
    assert stored.status is EscalationStatus.ENDED
    assert stored.ended_at == first


async def test_ending_what_is_not_there_says_so(contexts: SqlEscalationContextRepository) -> None:
    await contexts.claim(ALICE, a_context())
    assert not await contexts.mark_ended(BOB, CALL, RAISED)
    assert not await contexts.mark_ended(ALICE, CallId("call-2"), RAISED)


async def test_an_end_before_the_escalation_is_stored_as_the_escalation(
    contexts: SqlEscalationContextRepository,
) -> None:
    await contexts.claim(ALICE, a_context())
    assert await contexts.mark_ended(ALICE, CALL, RAISED - timedelta(seconds=1))
    stored = await contexts.get(ALICE, CALL)
    assert stored is not None
    assert stored.status is EscalationStatus.ENDED
    assert stored.ended_at == RAISED


async def test_deleting_a_user_takes_their_contexts_with_them(
    session: AsyncSession, contexts: SqlEscalationContextRepository
) -> None:
    await contexts.claim(ALICE, a_context())
    await session.execute(delete(UserRow).where(UserRow.id == ALICE.value))
    session.expunge_all()
    assert await contexts.get(ALICE, CALL) is None
