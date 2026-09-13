"""The one component that owns every call's life (D-029).

It reads every line's events, gives each new call a run and each later event to that call's run,
and implements the agent's `CallActions` by handing each request to the run of the call it is for.
There is one orchestrator whatever the transport: what differs between transports is the plan each
call is given, derived from capabilities, never a branch on which transport it is.

A deployment may carry calls on several lines — one per country it serves, say — and still has one
orchestrator. A call belongs to the line it arrived on for its whole life: its plan is derived from
that line's transport, its owner is found by that line's ownership, and it is dialled into, bridged
and ended there, so a user is rung from a number in the region the call reached.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import CallActions
from letmehandle.application.orchestration.inputs import (
    Abandoned,
    EndingRequested,
    EscalationRequested,
    MessageTaken,
    OutcomeRecorded,
    Reported,
)
from letmehandle.application.orchestration.plan import plan_for
from letmehandle.application.orchestration.ports import Assistance, Bounds
from letmehandle.application.orchestration.recovery import Recovery
from letmehandle.application.orchestration.run import (
    DEGRADED,
    DUPLICATE_IGNORED,
    CallIsOverError,
    CallRun,
    CallStanding,
    RunContext,
)
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import datetime

    from letmehandle.application.agent.ports import CallEnding, OutcomeRecord
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.inputs import Request
    from letmehandle.application.orchestration.ports import (
        AssistantServices,
        CallLine,
        OpenCallStores,
    )
    from letmehandle.application.resilience.circuit import Circuits
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.policy.escalation import EscalationProposal
    from letmehandle.domain.ports.call_transport import CallEvent, CallTransport
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.tracing import Tracer

logger = get_logger(__name__)

# A request for a run, made once the future its answer arrives on exists.
type Asking = Callable[[asyncio.Future[None]], Request]

# How many ended calls are remembered, so an event for one arriving after its teardown is dropped
# rather than taken for a new call. Bounded: a process runs for weeks.
REMEMBERED_ENDINGS: Final = 10_000

# How many live calls one account may have. More than a person has at once on any line, and few
# enough that a handset reporting new calls in a loop holds a handful of runs, not thousands.
LIVE_CALLS_PER_ACCOUNT: Final = 5


class CallOrchestrator:
    """Owns every live call on its lines, from arrival to teardown.

    `summariser` writes the summary of a call the assistant took; without one, every call is
    summarised from its facts.
    """

    def __init__(
        self,
        *,
        lines: Sequence[CallLine],
        stores: OpenCallStores,
        dispatcher: EscalationDispatcher,
        clock: Clock,
        metrics: MetricsRecorder,
        tracer: Tracer,
        circuits: Circuits,
        assistant: AssistantServices | None,
        summariser: CallSummariser | None,
        bounds: Bounds | None = None,
    ) -> None:
        if not lines:
            raise InvariantError("an orchestrator needs a line for calls to arrive on")
        for line in lines:
            capabilities = line.transport.capabilities
            if assistant is None and (
                capabilities.supports_agent_conversation
                and capabilities.can_answer_under_program_control
            ):
                raise InvariantError(
                    "a transport the assistant can take calls on needs a speech service and an "
                    "agent"
                )
        # What every run shares, built on the first line; each line's runs are given a copy of it
        # with that line's transport and owners, and nothing reads a transport from this one.
        first = lines[0]
        self._context = RunContext(
            transport=first.transport,
            ownership=first.ownership,
            stores=stores,
            dispatcher=dispatcher,
            clock=clock,
            metrics=metrics,
            tracer=tracer,
            circuits=circuits,
            bounds=bounds or Bounds(),
            summariser=summariser,
            admits=self._admits,
        )
        # What a run on each line is given: everything shared, and that line's transport and owners.
        self._contexts = {
            line.transport: replace(
                self._context, transport=line.transport, ownership=line.ownership
            )
            for line in lines
        }
        self._assistance = (
            None
            if assistant is None
            else Assistance(
                speech=assistant.speech,
                voices=assistant.voices,
                judging=assistant.judging(_RunActions(self._request)),
            )
        )
        self._runs: dict[CallId, CallRun] = {}
        self._tasks: dict[CallId, asyncio.Task[None]] = {}
        self._ended: OrderedDict[CallId, None] = OrderedDict()
        self._consumers: list[asyncio.Task[None]] = []

    @property
    def live_calls(self) -> int:
        """How many calls have a run."""
        return len(self._tasks)

    def standings(self) -> tuple[CallStanding, ...]:
        """Where every live call stands, oldest state first, so one stuck in a state stands out.

        A call still being matched to its owner has no standing yet, and is not listed.
        """
        standing = (run.standing for run in self._runs.values())
        return tuple(sorted((each for each in standing if each is not None), key=_since))

    async def start(self) -> None:
        """End what a previous process left unfinished, then take calls."""
        context = self._context
        await Recovery(
            transports=tuple(self._contexts),
            stores=context.stores,
            dispatcher=context.dispatcher,
            clock=context.clock,
            metrics=context.metrics,
            bounds=context.bounds,
        ).end_unfinished()
        loop = asyncio.get_running_loop()
        self._consumers = [loop.create_task(self._consume(line)) for line in self._contexts]

    async def stop(self) -> None:
        """Stop taking calls, tear every live one down, and wait for every run to finish."""
        consumers, self._consumers = self._consumers, []
        for consumer in consumers:
            consumer.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        await self._end(list(self._runs))

    async def end_calls_of(self, user_id: UserId) -> None:
        """Tear down every live call of this user's, and wait until each run has gone.

        For an account being deleted: once this returns, no run is left to write anything more
        about the user's calls. A call whose owner is still being looked up is nobody's yet; what
        it writes after the account is gone finds no account to write it against.
        """
        await self._end([call_id for call_id, run in self._runs.items() if run.owner == user_id])

    async def _end(self, call_ids: list[CallId]) -> None:
        """End these calls now and wait for their runs, within the shutdown bound.

        Each run is given the bound to reach its teardown; one still running after that is
        cancelled, and its call is left for the next start to end.
        """
        tasks = [self._tasks[call_id] for call_id in call_ids]
        for call_id in call_ids:
            self._runs[call_id].post(Abandoned())
        if not tasks:
            return
        _, pending = await asyncio.wait(
            tasks, timeout=self._context.bounds.shutdown.total_seconds()
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def receive(self, event: CallEvent, transport: CallTransport) -> None:
        """Give an event to its call's run, starting a run for a call that has just arrived.

        `transport` is the line the event arrived on, which a new call keeps for its whole life.
        A provider's call identifiers are its own and never repeat, so no two lines share one.
        """
        call_id = event.call_id
        run = self._runs.get(call_id)
        if run is not None:
            run.post(Reported(event))
            return
        if call_id in self._ended or event.kind is not CallEventKind.INCOMING:
            # About a call already torn down, or one this process never saw arrive: nothing to do.
            logger.info("call.event_ignored", kind=event.kind.value)
            self._context.metrics.increment(DUPLICATE_IGNORED, {"stage": "late"})
            return
        assistance = self._assistance_now()
        degraded = (
            (Dependency.SPEECH,) if assistance is None and self._assistance is not None else ()
        )
        run = CallRun(
            event,
            plan_for(transport, event, assistance),
            self._contexts[transport],
            degraded=degraded,
        )
        task = asyncio.get_running_loop().create_task(run.run())
        self._runs[call_id] = run
        self._tasks[call_id] = task
        task.add_done_callback(lambda done: self._run_done(call_id, done))

    def _assistance_now(self) -> Assistance | None:
        """What an assistant would speak with, unless speech is failing every call just now.

        A plan without it offers no assistant, and routing puts through to the user a call it would
        have handed to one: better their phone rings than the caller meets an assistant that cannot
        speak, or is hung up on while one fails to open.
        """
        if self._assistance is not None and self._context.circuits[Dependency.SPEECH].is_refusing:
            logger.warning("call.degraded", stage="speech")
            self._context.metrics.increment(DEGRADED, {"stage": "speech"})
            return None
        return self._assistance

    def _admits(self, user_id: UserId) -> bool:
        """Whether this account has fewer live calls than `LIVE_CALLS_PER_ACCOUNT`."""
        live = sum(1 for run in self._runs.values() if run.owner == user_id and not run.is_over)
        return live < LIVE_CALLS_PER_ACCOUNT

    async def _consume(self, transport: CallTransport) -> None:
        async for event in transport.events():
            self.receive(event, transport)

    def _run_done(self, call_id: CallId, task: asyncio.Task[None]) -> None:
        self._runs.pop(call_id, None)
        self._tasks.pop(call_id, None)
        self._ended[call_id] = None
        while len(self._ended) > REMEMBERED_ENDINGS:
            self._ended.popitem(last=False)
        if not task.cancelled() and task.exception() is not None:
            # A defect in a run. The call stays unfinished in storage, and the next start ends it.
            logger.error("call.run_failed", error=type(task.exception()).__name__)

    async def _request(self, call_id: CallId, make: Asking) -> None:
        run = self._runs.get(call_id)
        if run is None or run.is_over:
            raise CallIsOverError
        reply: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        run.post(make(reply))
        await asyncio.gather(reply)


def _since(standing: CallStanding) -> datetime:
    return standing.since


class _RunActions(CallActions):
    """The agent's actions, each handed to the run of the call it names and awaited there."""

    def __init__(
        self,
        request: Callable[[CallId, Asking], Awaitable[None]],
    ) -> None:
        self._request = request

    async def escalate(self, call_id: CallId, decision: EscalationDecision) -> None:
        await self._request(call_id, lambda reply: EscalationRequested(decision, reply))

    async def record_outcome(self, call_id: CallId, record: OutcomeRecord) -> None:
        await self._request(call_id, lambda reply: OutcomeRecorded(record, reply))

    async def take_message(self, call_id: CallId, message: str) -> None:
        await self._request(call_id, lambda reply: MessageTaken(message, reply))

    async def end_call(
        self, call_id: CallId, ending: CallEnding, assessment: EscalationProposal
    ) -> None:
        await self._request(call_id, lambda reply: EndingRequested(ending, assessment, reply))
