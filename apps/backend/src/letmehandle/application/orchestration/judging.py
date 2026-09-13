"""The agent's looks at one call: one at a time, each bounded, each reported as an input (D-029)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from letmehandle.application.orchestration.inputs import Judged
from letmehandle.application.orchestration.metrics import JUDGEMENT_FAILED, JUDGEMENT_SECONDS
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.application.resilience.timing import Stopwatch, within
from letmehandle.domain.failures import classify
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from letmehandle.application.agent.ports import CallAgent, CallSoFar
    from letmehandle.application.orchestration.inputs import Input
    from letmehandle.application.orchestration.run import RunContext

logger = get_logger(__name__)


class Judgements:
    """One call's looks by the agent, with one more owed when asked while a look runs."""

    def __init__(
        self,
        context: RunContext,
        *,
        post: Callable[[Input], None],
        note: Callable[[MarkKind, str], None],
    ) -> None:
        self._context = context
        self._post = post
        self._note = note
        self._task: asyncio.Task[None] | None = None
        self._owed = False

    def look(self, agent: CallAgent, so_far: Callable[[], CallSoFar]) -> None:
        """Start a look at the call as it stands, or owe one if a look is already running."""
        if self._task is not None and not self._task.done():
            self._owed = True
            return
        self._owed = False
        self._task = asyncio.get_running_loop().create_task(self._judge(agent, so_far()))

    def settle_owed(self) -> bool:
        """Whether a look was owed, which is no longer owed once asked."""
        owed, self._owed = self._owed, False
        return owed

    async def release(self) -> None:
        """Cancel a running look and wait for it to go."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _judge(self, agent: CallAgent, so_far: CallSoFar) -> None:
        context = self._context
        stopwatch = Stopwatch()
        try:
            with context.tracer.span("agent.judgement", dependency=Dependency.MODEL.value):
                judgement = await context.circuits[Dependency.MODEL].call(
                    lambda: within(context.bounds.judgement, lambda: agent.judge(so_far))
                )
        # A failed look changes nothing about the call: it is reported as no judgement.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.judgement_failed", error)
            kind = classify(error).kind
            context.metrics.increment(JUDGEMENT_FAILED, {"kind": kind})
            self._note(MarkKind.FAILURE, f"judgement.{kind}")
            context.metrics.observe(JUDGEMENT_SECONDS, stopwatch.seconds, {"outcome": "failed"})
            self._post(Judged(None))
        else:
            context.metrics.observe(JUDGEMENT_SECONDS, stopwatch.seconds, {"outcome": "judged"})
            self._post(Judged(judgement))
