"""Random calls: whatever happens, in whatever order, a call walks the state machine and ends.

Seeded, so a failure names the seed that reproduces it. Every step is something a provider, the
agent or the speech service can really do to a live call; the property is that no mixture of them
reaches a state the machine does not allow, leaves a call without its teardown, or leaves anything
running (which `orchestrating` counts on the way out).
"""

from __future__ import annotations

import asyncio
import random
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.agent.ports import CallEnding
from letmehandle.domain.models.call_state import ALLOWED, TERMINAL, CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation import (
    EscalationDecision,
    EscalationReason,
    EscalationUrgency,
)
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.ports.call_transport import ParticipantOutcome, ScreeningDecision
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.domain.ports.speech import SessionFailed, TranscriptProduced
from tests.support.orchestration import (
    WANTS_THE_USER,
    HandsetLine,
    Look,
    Running,
    StreamingLine,
    orchestrating,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

CALL = "call"
SEEDS = range(40)
STEPS = 12

type Step = Callable[[Running, random.Random], Awaitable[None]]


async def says(running: Running, chance: random.Random) -> None:
    if running.speech.sessions:
        await running.speech.sessions[0].emit(
            TranscriptProduced("Something.", speaker_is_caller=chance.random() < 0.8, is_final=True)
        )


async def speech_fails(running: Running, chance: random.Random) -> None:
    if running.speech.sessions:
        await running.speech.sessions[0].emit(SessionFailed("gone", retryable=False))


async def asks(running: Running, chance: random.Random) -> None:
    agent = running.agent.built
    assert agent is not None
    urgency = chance.choice(list(EscalationUrgency))
    decision = EscalationDecision.needed(EscalationReason.CALLER_ASKED_FOR_THE_USER, urgency)
    ending = chance.choice(list(CallEnding))
    request = (
        agent._actions.escalate(CallId(CALL), decision)
        if chance.random() < 0.7
        else agent._actions.end_call(CallId(CALL), ending)
    )
    # A refusal is an answer, not a failure of the property: the call may be over, or the dial may
    # have been refused.
    task = asyncio.get_running_loop().create_task(request)
    task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)


def reported(report: Callable[[StreamingLine, random.Random], object]) -> Step:
    async def step(running: Running, chance: random.Random) -> None:
        line = running.line
        assert isinstance(line, StreamingLine)
        report(line, chance)

    return step


STREAMING_STEPS: tuple[Step, ...] = (
    says,
    says,
    speech_fails,
    asks,
    asks,
    reported(lambda line, _: line.assistant_joins(CALL)),
    reported(lambda line, _: line.user_answers(CALL)),
    reported(
        lambda line, chance: line.user_unreachable(
            CALL,
            chance.choice(
                [
                    ParticipantOutcome.NO_ANSWER,
                    ParticipantOutcome.BUSY,
                    ParticipantOutcome.FAILED,
                    ParticipantOutcome.ANSWERED_BY_MACHINE,
                ]
            ),
        )
    ),
    reported(lambda line, chance: line.leaves(CALL, chance.choice(list(Leg)))),
    reported(lambda line, _: line.report_unreachable_assistant(CALL)),
    reported(lambda line, _: line.hangs_up(CALL)),
    reported(lambda line, _: line.report_failure(CALL)),
    reported(
        lambda line, chance: line.audio[CallId(CALL)].stop() if chance.random() < 0.3 else None
    ),
)


def a_legal_walk(states: list[CallState]) -> bool:
    return states[0] is CallState.RECEIVED and all(
        after in ALLOWED[before] for before, after in pairwise(states)
    )


async def ends_properly(running: Running) -> None:
    running.line.hangs_up(CALL)
    call = await running.ended(CALL)
    states = running.stores.states(CALL)
    assert a_legal_walk(states), states
    assert call.state in TERMINAL
    assert states.count(call.state) == 1
    assert running.line.asked("terminate", CALL) == 1


@pytest.mark.parametrize("seed", SEEDS)
async def test_a_random_streaming_call_walks_the_machine_and_ends(seed: int) -> None:
    chance = random.Random(seed)  # noqa: S311 - a reproducible sequence, not a secret
    posture = chance.choice(list(HandlingPosture))
    preferences = UserPreferences(rules=CallRules(default_posture=posture))
    looks = [Look(proposal=WANTS_THE_USER) if chance.random() < 0.5 else Look() for _ in range(4)]
    line = StreamingLine()
    if chance.random() < 0.2:
        line.refusing.add(chance.choice(["dial", "answer", "cancel"]))
    async with orchestrating(line, preferences=preferences, looks=looks) as running:
        line.arrives(CALL, Caller(number=PhoneNumber("+12025550101")))
        for _ in range(STEPS):
            await chance.choice(STREAMING_STEPS)(running, chance)
            await asyncio.sleep(chance.choice([0, 0, 0.001, 0.02]))
        await ends_properly(running)


@pytest.mark.parametrize("seed", SEEDS)
async def test_a_random_handset_call_walks_the_machine_and_ends(seed: int) -> None:
    chance = random.Random(seed)  # noqa: S311 - a reproducible sequence, not a secret
    line = HandsetLine()
    async with orchestrating(line) as running:
        line.arrives(CALL, chance.choice([None, *ScreeningDecision]))
        for _ in range(chance.randrange(4)):
            chance.choice([line.picked_up, line.hangs_up, line.report_failure])(CALL)
            await asyncio.sleep(chance.choice([0, 0.001]))
        await ends_properly(running)
        assert running.speech.sessions == []
