"""Call actions that keep what they are asked to do, so a test can read it back.

No kinder than orchestration will be. It refuses to escalate on a decision that does not require
it, because the port says it is only ever called with one, and it refuses anything at all once the
call has been hung up, because a call that has ended has nobody left on it to act for. A fake that
accepted both would let a tool that does either pass every test here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from letmehandle.application.agent.ports import CallActions
from letmehandle.domain.errors import IllegalTransitionError, InvariantError

if TYPE_CHECKING:
    import asyncio

    from letmehandle.application.agent.ports import CallEnding, OutcomeRecord
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId


@dataclass(frozen=True, slots=True)
class Escalated:
    call_id: CallId
    decision: EscalationDecision


@dataclass(frozen=True, slots=True)
class Recorded:
    call_id: CallId
    record: OutcomeRecord


@dataclass(frozen=True, slots=True)
class MessageTaken:
    call_id: CallId
    message: str


@dataclass(frozen=True, slots=True)
class Ended:
    call_id: CallId
    ending: CallEnding


type Action = Escalated | Recorded | MessageTaken | Ended


@dataclass
class RecordingCallActions(CallActions):
    """Every action, in order.

    `escalation_failures` are raised, one per escalation and in order, instead of reaching anyone.
    `escalation_gate`, when set, holds every escalation until it is opened — which is how a test
    puts two escalations in flight at the same moment without sleeping.
    """

    actions: list[Action] = field(default_factory=list)
    escalation_failures: list[Exception] = field(default_factory=list)
    escalation_gate: asyncio.Event | None = None
    escalations_started: int = 0

    async def escalate(self, call_id: CallId, decision: EscalationDecision) -> None:
        self._still_on(call_id)
        if not decision.required:
            raise InvariantError("escalate is only ever called with a decision to escalate")
        self.escalations_started += 1
        if self.escalation_gate is not None:
            await self.escalation_gate.wait()
        if self.escalation_failures:
            raise self.escalation_failures.pop(0)
        self.actions.append(Escalated(call_id, decision))

    async def record_outcome(self, call_id: CallId, record: OutcomeRecord) -> None:
        self._still_on(call_id)
        self.actions.append(Recorded(call_id, record))

    async def take_message(self, call_id: CallId, message: str) -> None:
        self._still_on(call_id)
        self.actions.append(MessageTaken(call_id, message))

    async def end_call(self, call_id: CallId, ending: CallEnding) -> None:
        self._still_on(call_id)
        self.actions.append(Ended(call_id, ending))

    def of_kind[A: Action](self, kind: type[A]) -> list[A]:
        return [action for action in self.actions if isinstance(action, kind)]

    def _still_on(self, call_id: CallId) -> None:
        if any(isinstance(action, Ended) and action.call_id == call_id for action in self.actions):
            raise IllegalTransitionError("ended", "another action")
