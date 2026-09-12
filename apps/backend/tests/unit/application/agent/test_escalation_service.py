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
from letmehandle.domain.models.escalation import EscalationReason, EscalationUrgency
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from tests.support.recording_call_actions import Escalated, RecordingCallActions
from tests.unit.application.agent.calls import RULES, THREE_AM, a_call

URGENT = EscalationProposal(importance=CallImportance.URGENT, intent=CallIntent.PERSONAL)
NOTABLE = EscalationProposal(importance=CallImportance.NOTABLE, intent=CallIntent.ENQUIRY)
ROUTINE = EscalationProposal(importance=CallImportance.ROUTINE, intent=CallIntent.ENQUIRY)


def service() -> tuple[EscalationService, RecordingCallActions]:
    actions = RecordingCallActions()
    return EscalationService(actions), actions


def test_the_circumstances_are_the_calls_rules_and_grant() -> None:
    call = a_call(from_important_contact=True, now=THREE_AM)

    circumstances = circumstances_of(call)

    assert circumstances.rules is RULES
    assert circumstances.authority is call.authority
    assert circumstances.now == THREE_AM
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


async def test_a_call_escalated_for_later_rings_once_more_when_it_becomes_urgent() -> None:
    escalation, actions = service()
    night = a_call(now=THREE_AM)

    first = await escalation.consider(night, NOTABLE)
    await escalation.consider(night, NOTABLE)
    second = await escalation.consider(night, URGENT)
    await escalation.consider(night, URGENT)
    await escalation.consider(night, NOTABLE)

    assert first.urgency is EscalationUrgency.WHILE_CONVENIENT
    assert second.urgency is EscalationUrgency.IMMEDIATE
    assert [each.decision for each in actions.of_kind(Escalated)] == [first, second]


async def test_a_call_already_rung_immediately_is_never_downgraded_into_another_ring() -> None:
    escalation, actions = service()
    night = a_call(now=THREE_AM)

    await escalation.consider(night, URGENT)
    later = await escalation.consider(night, NOTABLE)

    assert later.urgency is EscalationUrgency.WHILE_CONVENIENT
    assert len(actions.actions) == 1


async def test_a_failed_escalation_leaves_the_call_able_to_reach_the_user() -> None:
    escalation, actions = service()
    call = a_call()
    actions.escalation_failures = [ConnectionError("the notification did not send")]

    with pytest.raises(ConnectionError):
        await escalation.consider(call, URGENT)

    await escalation.consider(call, URGENT)
    assert len(actions.actions) == 1


async def test_a_failed_upgrade_leaves_the_earlier_escalation_standing() -> None:
    escalation, actions = service()
    night = a_call(now=THREE_AM)
    await escalation.consider(night, NOTABLE)
    actions.escalation_failures = [ConnectionError("the notification did not send")]

    with pytest.raises(ConnectionError):
        await escalation.consider(night, URGENT)

    # The note for later still stands, so another one is not made...
    await escalation.consider(night, NOTABLE)
    assert len(actions.actions) == 1
    # ...and the upgrade can still be.
    retried = await escalation.consider(night, URGENT)
    assert retried.is_immediate
    assert len(actions.actions) == 2


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


async def test_an_upgrade_that_reached_the_user_survives_an_earlier_look_failing() -> None:
    # The first look is still ringing "while convenient" when the urgent one arrives. The first
    # fails; the urgent one reaches the user. The call has been escalated immediately, and asking
    # again must not ring a third time.
    escalation, actions = service()
    actions.escalation_gate = asyncio.Event()
    actions.escalation_failures = [ConnectionError("the notification did not send")]
    night = a_call(now=THREE_AM)

    earlier = asyncio.create_task(escalation.consider(night, NOTABLE))
    upgrade = asyncio.create_task(escalation.consider(night, URGENT))
    await settle()
    actions.escalation_gate.set()
    failed, reached = await asyncio.gather(earlier, upgrade, return_exceptions=True)

    assert isinstance(failed, ConnectionError)
    assert not isinstance(reached, BaseException)
    assert reached.is_immediate
    await escalation.consider(night, URGENT)
    assert [each.decision for each in actions.of_kind(Escalated)] == [reached]


async def test_two_looks_that_both_fail_leave_the_call_able_to_reach_the_user() -> None:
    escalation, actions = service()
    actions.escalation_gate = asyncio.Event()
    actions.escalation_failures = [ConnectionError("first"), ConnectionError("second")]
    night = a_call(now=THREE_AM)

    looks = [
        asyncio.create_task(escalation.consider(night, NOTABLE)),
        asyncio.create_task(escalation.consider(night, URGENT)),
    ]
    await settle()
    actions.escalation_gate.set()
    outcomes = await asyncio.gather(*looks, return_exceptions=True)

    assert all(isinstance(outcome, ConnectionError) for outcome in outcomes)
    # Nobody was reached, so a call that merits only a later note still gets one.
    later = await escalation.consider(night, NOTABLE)
    assert actions.of_kind(Escalated) == [Escalated(night.call_id, later)]


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
