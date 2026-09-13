"""Acting on a finished judgement: the escalation first, then an ending only if it is still allowed.

Each test writes into the notes what a model asked for, concludes on an assessment, and reads back
what happened to the call. The rows are the endings a model can ask for against the escalations the
rules can make of the same call.
"""

from __future__ import annotations

from typing import Final

import pytest

from letmehandle.application.agent.ports import CallEnding, ToolRefusal
from letmehandle.domain.models.authority import AgentAuthority, Capability
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from tests.support.recording_call_actions import Ended, Escalated
from tests.unit.application.agent.calls import a_call, deferring
from tests.unit.application.agent.kit import Kit

MAY_DECLINE: Final = AgentAuthority.granting(Capability.DECLINE_ON_THE_USERS_BEHALF)

ROUTINE: Final = EscalationProposal(importance=CallImportance.ROUTINE, intent=CallIntent.SALES)
NOTABLE: Final = EscalationProposal(importance=CallImportance.NOTABLE, intent=CallIntent.ENQUIRY)
URGENT: Final = EscalationProposal(importance=CallImportance.URGENT, intent=CallIntent.PERSONAL)

NOT_ENDED_FOR_THE_RULES: Final = (
    "the user's rules call for reaching the user, so the call was not ended"
)
NOT_HANDED_OVER: Final = "the user was not reached for this call, so it was not handed over"


@pytest.mark.parametrize(
    ("ending", "assessment", "deferred", "refused_because"),
    [
        pytest.param(CallEnding.RESOLVED, ROUTINE, False, None, id="resolved, nobody needed"),
        pytest.param(CallEnding.DECLINED, ROUTINE, False, None, id="declined, nobody needed"),
        pytest.param(
            CallEnding.RESOLVED, URGENT, False, NOT_ENDED_FOR_THE_RULES, id="resolved, user needed"
        ),
        pytest.param(CallEnding.DECLINED, NOTABLE, True, None, id="declined, a note for later"),
        pytest.param(
            CallEnding.DECLINED,
            URGENT,
            True,
            NOT_ENDED_FOR_THE_RULES,
            id="declined, urgent while others wait",
        ),
        pytest.param(CallEnding.HANDED_OVER, URGENT, False, None, id="handed over, user reached"),
        pytest.param(
            CallEnding.HANDED_OVER, NOTABLE, True, NOT_HANDED_OVER, id="handed over, a note due"
        ),
        pytest.param(
            CallEnding.HANDED_OVER, ROUTINE, False, NOT_HANDED_OVER, id="handed over, nobody"
        ),
    ],
)
async def test_an_ending_is_applied_only_where_the_escalation_allows_it(
    ending: CallEnding,
    assessment: EscalationProposal,
    deferred: bool,
    refused_because: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if deferred:
        deferring(monkeypatch)
    kit = Kit()
    call = a_call(authority=MAY_DECLINE)
    kit.notes.ending_requested(ending)

    judgement = await kit.conclusion.conclude(call, kit.notes, assessment)

    escalated = kit.actions.of_kind(Escalated)
    assert escalated == (
        [Escalated(call.call_id, judgement.escalation)] if judgement.escalation.required else []
    )
    if refused_because is None:
        assert kit.actions.of_kind(Ended) == [Ended(call.call_id, ending)]
        assert judgement.ended
        assert judgement.refusals == ()
    else:
        assert kit.actions.of_kind(Ended) == []
        assert not judgement.ended
        assert judgement.refusals == (ToolRefusal("end_call", refused_because),)


async def test_the_escalation_happens_before_the_ending() -> None:
    kit = Kit()
    call = a_call()
    kit.notes.ending_requested(CallEnding.HANDED_OVER)

    await kit.conclusion.conclude(call, kit.notes, URGENT)

    assert [type(action) for action in kit.actions.actions] == [Escalated, Ended]


async def test_a_withheld_ending_is_refused_whatever_the_rules_allow() -> None:
    kit = Kit()
    call = a_call()
    kit.notes.ending_requested(CallEnding.RESOLVED)

    judgement = await kit.conclusion.conclude(
        call, kit.notes, ROUTINE, ending_withheld="the judgement did not finish"
    )

    assert kit.actions.actions == []
    assert judgement.refusals == (ToolRefusal("end_call", "the judgement did not finish"),)


async def test_with_no_ending_asked_for_the_call_is_left_alone() -> None:
    kit = Kit()

    judgement = await kit.conclusion.conclude(a_call(), kit.notes, ROUTINE)

    assert kit.actions.actions == []
    assert not judgement.ended
    assert judgement.refusals == ()


async def test_the_most_pressing_reading_is_escalated_and_the_assessment_is_reported() -> None:
    kit = Kit()
    call = a_call()
    kit.notes.escalation_requested(ROUTINE)
    kit.notes.escalation_requested(URGENT)

    judgement = await kit.conclusion.conclude(call, kit.notes, ROUTINE)

    assert judgement.proposal == ROUTINE
    assert judgement.escalation.is_immediate
    assert len(kit.actions.of_kind(Escalated)) == 1


async def test_a_failure_to_reach_the_user_is_raised_before_any_ending() -> None:
    kit = Kit()
    kit.actions.escalation_failures = [ConnectionError("dialler down")]
    kit.notes.ending_requested(CallEnding.HANDED_OVER)

    with pytest.raises(ConnectionError, match="dialler down"):
        await kit.conclusion.conclude(a_call(), kit.notes, URGENT)

    assert kit.actions.actions == []
    assert not kit.notes.ended
