"""The one component that owns every call's life (D-029).

It reads the transport's events, gives each new call a run and each later event to that call's run,
and implements the agent's `CallActions` by handing each request to the run of the call it is for.
There is one orchestrator whatever the transport: what differs between transports is the plan each
call is given, derived from capabilities, never a branch on which transport it is.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
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
from letmehandle.application.orchestration.run import CallIsOverError, CallRun, RunContext
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.call_transport import CallEventKind
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from letmehandle.application.agent.ports import CallEnding, OutcomeRecord
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.inputs import Request
    from letmehandle.application.orchestration.ports import (
        AssistantServices,
        CallOwnership,
        OpenCallStores,
    )
    from letmehandle.domain.models.escalation import EscalationDecision
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.policy.escalation import EscalationProposal
    from letmehandle.domain.ports.call_transport import CallEvent, CallTransport
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

# A request for a run, made once the future its answer arrives on exists.
type Asking = Callable[[asyncio.Future[None]], Request]

# How many ended calls are remembered, so an event for one arriving after its teardown is dropped
# rather than taken for a new call. Bounded: a process runs for weeks.
REMEMBERED_ENDINGS: Final = 10_000


class CallOrchestrator:
    """Owns every live call on one transport, from arrival to teardown.

    `summariser` writes the summary of a call the assistant took; without one, every call is
    summarised from its facts.
    """

    def __init__(
        self,
        *,
        transport: CallTransport,
        ownership: CallOwnership,
        stores: OpenCallStores,
        dispatcher: EscalationDispatcher,
        clock: Clock,
        metrics: MetricsRecorder,
        assistant: AssistantServices | None,
        summariser: CallSummariser | None,
        bounds: Bounds | None = None,
    ) -> None:
        capabilities = transport.capabilities
        if assistant is None and (
            capabilities.supports_agent_conversation
            and capabilities.can_answer_under_program_control
        ):
            raise InvariantError(
                "a transport the assistant can take calls on needs a speech service and an agent"
            )
        self._transport = transport
        self._context = RunContext(
            transport=transport,
            ownership=ownership,
            stores=stores,
            dispatcher=dispatcher,
            clock=clock,
            metrics=metrics,
            bounds=bounds or Bounds(),
            summariser=summariser,
        )
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
        self._consumer: asyncio.Task[None] | None = None

    @property
    def live_calls(self) -> int:
        """How many calls have a run."""
        return len(self._tasks)

    async def start(self) -> None:
        """End what a previous process left unfinished, then take calls."""
        context = self._context
        await Recovery(
            transport=context.transport,
            stores=context.stores,
            dispatcher=context.dispatcher,
            clock=context.clock,
            metrics=context.metrics,
            bounds=context.bounds,
        ).end_unfinished()
        self._consumer = asyncio.get_running_loop().create_task(self._consume())

    async def stop(self) -> None:
        """Stop taking calls, tear every live one down, and wait for every run to finish."""
        consumer, self._consumer = self._consumer, None
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
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

    def receive(self, event: CallEvent) -> None:
        """Give an event to its call's run, starting a run for a call that has just arrived."""
        call_id = event.call_id
        run = self._runs.get(call_id)
        if run is not None:
            run.post(Reported(event))
            return
        if call_id in self._ended or event.kind is not CallEventKind.INCOMING:
            # About a call already torn down, or one this process never saw arrive: nothing to do.
            logger.info("call.event_ignored", kind=event.kind.value)
            return
        run = CallRun(event, plan_for(self._transport, event, self._assistance), self._context)
        task = asyncio.get_running_loop().create_task(run.run())
        self._runs[call_id] = run
        self._tasks[call_id] = task
        task.add_done_callback(lambda done: self._run_done(call_id, done))

    async def _consume(self) -> None:
        async for event in self._transport.events():
            self.receive(event)

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
        await reply


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
