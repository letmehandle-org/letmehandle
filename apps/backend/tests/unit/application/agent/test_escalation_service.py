"""The one path to the user's phone.

What these tests count is rings. The policy's decisions are tested beside the policy; here the
question is only how many times a call reaches the user, and whether a failure to reach them
leaves the call unable to reach them again.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from letmehandle.application.agent.escalation import EscalationService, circumstances_of
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from tests.support.recording_call_actions import Escalated, RecordingCallActions
from tests.unit.application.agent.calls import RULES, a_call

URGENT = EscalationProposal(importance=CallImportance.URGENT, intent=CallIntent.PERSONAL)
NOTABLE = EscalationProposal(importance=CallImportance.NOTABLE, intent=CallIntent.ENQUIRY)
ROUTINE = EscalationProposal(importance=CallImportance.ROUTINE, intent=CallIntent.ENQUIRY)


def service() -> tuple[EscalationService, RecordingCallActions]:
    actions = RecordingCallActions()
    return EscalationService(actions), actions


def test_the_circumstances_are_the_calls_rules_and_grant() -> None:
    call = a_call(from_important_contact=True)

    circumstances = circumstances_of(call)

    assert circumstances.rules is RULES
    assert circumstances.authority is call.authority
    assert circumstances.from_important_contact


async def test_a_decision_to_escalate_reaches_the_user() -> None:
    escalation, actions = service()
    call = a_call()

    decision = await escalation.consider(call, NOTABLE)

    assert decision.required
    assert decision.reason is EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT
    assert actions.actions == [Escalated(call.call_id, decision)]


async def test_a_decision_not_to_escalate_reaches_nobody() -> None:
    escalation, actions = service()
    call = a_call()

    decision = await escalation.consider(call, ROUTINE)

    assert not decision.required
    assert actions.actions == []


async def test_asking_again_does_not_ring_again() -> None:
    escalation, actions = service()
    call = a_call()

    for _ in range(5):
        decision = await escalation.consider(call, URGENT)
        assert decision.required

    assert len(actions.actions) == 1


async def test_each_call_is_escalated_on_its_own() -> None:
    escalation, actions = service()

    await escalation.consider(a_call(call_id="call-1"), URGENT)
    await escalation.consider(a_call(call_id="call-2"), URGENT)

    assert [each.call_id.value for each in actions.of_kind(Escalated)] == ["call-1", "call-2"]


async def test_a_failed_escalation_leaves_the_call_able_to_reach_the_user() -> None:
    escalation, actions = service()
    call = a_call()
    actions.escalation_failures = [ConnectionError("the notification did not send")]

    with pytest.raises(ConnectionError):
        await escalation.consider(call, URGENT)

    await escalation.consider(call, URGENT)
    assert len(actions.actions) == 1


async def settle() -> None:
    """Let every task run until each is waiting on something that has not happened yet."""
    for _ in range(20):
        await asyncio.sleep(0)


async def test_two_looks_at_the_same_moment_ring_once() -> None:
    escalation, actions = service()
    actions.escalation_gate = asyncio.Event()
    call = a_call()

    first = asyncio.create_task(escalation.consider(call, URGENT))
    second = asyncio.create_task(escalation.consider(replace(call), URGENT))
    await settle()
    actions.escalation_gate.set()
    await asyncio.gather(first, second)

    assert actions.escalations_started == 1
    assert len(actions.actions) == 1


async def test_two_looks_that_both_fail_leave_the_call_able_to_reach_the_user() -> None:
    escalation, actions = service()
    actions.escalation_gate = asyncio.Event()
    actions.escalation_failures = [ConnectionError("first"), ConnectionError("second")]
    call = a_call()

    looks = [
        asyncio.create_task(escalation.consider(call, NOTABLE)),
        asyncio.create_task(escalation.consider(replace(call), URGENT)),
    ]
    await settle()
    actions.escalation_gate.set()
    outcomes = await asyncio.gather(*looks, return_exceptions=True)

    assert all(isinstance(outcome, ConnectionError) for outcome in outcomes)
    # Nobody was reached, so the next look still reaches the user.
    reached = await escalation.consider(call, NOTABLE)
    assert actions.of_kind(Escalated) == [Escalated(call.call_id, reached)]


async def test_a_forgotten_call_can_reach_the_user_again() -> None:
    # What orchestration does when a call is over: nothing about it is kept, per call, for ever.
    escalation, actions = service()
    call = a_call()
    await escalation.consider(call, URGENT)

    escalation.forget(call.call_id)
    await escalation.consider(call, URGENT)

    assert len(actions.of_kind(Escalated)) == 2


def test_forgetting_a_call_nothing_remembers_is_harmless() -> None:
    escalation, _ = service()

    escalation.forget(a_call().call_id)
