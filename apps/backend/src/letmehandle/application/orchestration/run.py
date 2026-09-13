"""One live call, from the moment it arrives until it is torn down.

A run reads its inbox one input at a time, and nothing else changes its call, so two things that
happen at once to one call are two inputs in order rather than an interleaving. What a run waits on
outside its inbox — a conversation, a judgement, a timer — is a task the run owns, and reports back
as another input. Every wait is bounded, and running out is an input like any other (D-029).

The state machine, as a run drives it:

    arrival        RECEIVED → ROUTING → REJECTED, PASSTHROUGH or AGENT_HANDLING
    PASSTHROUGH    → COMPLETED when the user is not reached, or either of them hangs up
    AGENT_HANDLING → ESCALATION_REQUESTED when an escalation is to ring the user now
    ESCALATION_REQUESTED → HUMAN_RINGING once the user is being dialled,
                         → AGENT_HANDLING when the dial is refused
    HUMAN_RINGING  → HUMAN_JOINED when the user answers,
                   → AGENT_HANDLING when they do not — no answer, busy, failed, a machine, or the
                     ring running out — with that outcome in the assistant's context
    HUMAN_JOINED   → COMPLETED when the user or the caller leaves
    any            → COMPLETED when the caller hangs up or the agent ends the call,
                   → FAILED when the transport or the assistant fails, the call outlasts the
                     longest a call may last, or the process stops
    RECEIVED       → FAILED when the account already has as many live calls as it may

Every ending goes through `_finish`, the one teardown.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.ports import CallEnding, CallSoFar
from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.orchestration.inputs import (
    Abandoned,
    CallRanTooLong,
    ConversationStopped,
    EndingRequested,
    EscalationRequested,
    Heard,
    Judged,
    MessageTaken,
    OutcomeRecorded,
    Reported,
    RingRanOut,
    SilenceRanOut,
)
from letmehandle.application.orchestration.ledger import CallLedger, reported_instant
from letmehandle.application.orchestration.plan import DialTheUser, LetItRing
from letmehandle.application.orchestration.routing import Route, route_on
from letmehandle.application.orchestration.speaking import Situation, Speaking, UserReach
from letmehandle.application.orchestration.summary import Findings, facts_of, with_findings
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.application.resilience.retry import RetryPolicy, retry_idempotent
from letmehandle.application.resilience.timing import Stopwatch, within
from letmehandle.application.speech.conversation import ConversationEnd
from letmehandle.domain.errors import DomainError
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.models.call import CallHandling, CallSession, ParticipantRole, Speaker
from letmehandle.domain.models.call_state import TERMINAL, CallState
from letmehandle.domain.models.caller import Caller, CallerCategory
from letmehandle.domain.models.escalation_context import (
    MAX_CALL_ID_LENGTH,
    MAX_DETAIL_LENGTH,
    EscalationContext,
)
from letmehandle.domain.models.preferences import UserPreferences
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.domain.policy.routing import route
from letmehandle.domain.ports.call_transport import CallEventKind, ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.observability import catalogue
from letmehandle.observability.logging import bind_call, get_logger, log_failure
from letmehandle.observability.tracing import CALL_ID

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime, timedelta

    from letmehandle.application.agent.ports import AgentJudgement
    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.inputs import Input, Request
    from letmehandle.application.orchestration.plan import CallPlan, Converse
    from letmehandle.application.orchestration.ports import (
        Bounds,
        CallLine,
        CallStores,
        OpenCallStores,
    )
    from letmehandle.application.resilience.circuit import Circuits
    from letmehandle.application.speech.conversation import TranscriptTurn
    from letmehandle.domain.models.escalation import EscalationDecision, EscalationReason
    from letmehandle.domain.models.identifiers import CallId, EventId, UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.call_transport import CallEvent
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.tracing import Tracer

logger = get_logger(__name__)

# What a run asks of the transport, each its own stage of the call.
_TELEPHONY_STAGES: Final = frozenset({"answer", "dial", "cancel", "terminate"})

PROVIDER_FAILED: Final = catalogue.count(
    "call.provider_failed", stage={"owner", "speech", *_TELEPHONY_STAGES}, kind=FailureKind
)
JUDGEMENT_FAILED: Final = catalogue.count("call.judgement_failed", kind=FailureKind)
SUMMARY_FAILED: Final = catalogue.count("call.summary_failed", kind=FailureKind)
CALL_ENDED: Final = catalogue.count("call.ended", outcome=TERMINAL)
# A call ended by a bound on calls themselves rather than by anything that happened on it.
CALL_BOUNDED: Final = catalogue.count("call.bounded", kind={"duration", "live_calls"})
ROUTED: Final = catalogue.count("call.routed", outcome={*Route, "nobody"})
# How an escalation that rang the user turned out: how their phone answered, or that it never rang,
# or that the call ended while it did.
ESCALATION_RESOLVED: Final = catalogue.count(
    "call.escalation_resolved", outcome={*ParticipantOutcome, "dial_refused", "call_ended"}
)
# A transport event this run had already acted on, delivered again.
DUPLICATE_IGNORED: Final = catalogue.count("call.duplicate_ignored", stage={"repeated", "late"})
# A call handled without a dependency whose circuit was open: put through with no assistant, or
# summarised from its facts with no model.
DEGRADED: Final = catalogue.count("call.degraded", stage={"speech", "summary"})

PROVIDER_SECONDS: Final = catalogue.measure("call.provider_seconds", stage=_TELEPHONY_STAGES)
SPEECH_OPEN_SECONDS: Final = catalogue.measure(
    "call.speech_open_seconds", outcome={"opened", "failed"}
)
JUDGEMENT_SECONDS: Final = catalogue.measure("call.judgement_seconds", outcome={"judged", "failed"})
SUMMARY_SECONDS: Final = catalogue.measure("call.summary_seconds", outcome={"written", "fallback"})

# Ending a call at the transport is safe to ask twice, so a timeout or an unreachable provider is
# worth another try: a call left up at the provider is a caller left on a line nobody is on.
TERMINATE_RETRY: Final = RetryPolicy(attempts=3)

# While the assistant is on the call and the user is not, something the caller says is worth
# another look. Once the user has joined, the call is theirs to handle.
_JUDGED_IN: Final = frozenset(
    {CallState.AGENT_HANDLING, CallState.ESCALATION_REQUESTED, CallState.HUMAN_RINGING}
)

# Where the user is being reached, and may yet not be.
_USER_BEING_REACHED: Final = frozenset({CallState.ESCALATION_REQUESTED, CallState.HUMAN_RINGING})


class CallIsOverError(DomainError):
    """Something was asked of a call that has already been torn down."""

    failure_kind = FailureKind.CONFLICT

    def __init__(self) -> None:
        super().__init__("the call is over, so nothing more can be done on it")


class DialRefusedError(DomainError):
    """The user could not be dialled, so the escalation did not reach them."""

    failure_kind = FailureKind.REFUSED

    def __init__(self) -> None:
        super().__init__("the user could not be dialled into the call")


@dataclass(frozen=True, slots=True)
class RunContext:
    """What every run shares, for the life of the orchestrator."""

    stores: OpenCallStores
    dispatcher: EscalationDispatcher
    clock: Clock
    metrics: MetricsRecorder
    tracer: Tracer
    circuits: Circuits
    bounds: Bounds
    summariser: CallSummariser | None
    # Whether an account has room for another live call.
    admits: Callable[[UserId], bool]


@dataclass(frozen=True, slots=True)
class CallStanding:
    """Where one live call is, and since when. A call long in one state is a call stuck in it."""

    call_id: CallId
    state: CallState
    since: datetime


@dataclass(frozen=True, slots=True)
class Owner:
    """The user a call is for, as far as a run needs them."""

    user_id: UserId
    number: PhoneNumber
    preferences: UserPreferences


class CallRun:
    """One call's inbox, state and teardown."""

    def __init__(
        self,
        incoming: CallEvent,
        plan: CallPlan,
        line: CallLine,
        context: RunContext,
        *,
        degraded: tuple[Dependency, ...] = (),
    ) -> None:
        self._incoming = incoming
        # What the plan was made without, because its circuit was open when the call arrived.
        self._degraded = degraded
        self._plan = plan
        self._line = line
        self._context = context
        self._inbox: asyncio.Queue[Input] = asyncio.Queue()
        self._seen: set[EventId] = {incoming.event_id}
        self._speaking = Speaking(
            call_id=incoming.call_id, post=self.post, clock=context.clock, metrics=context.metrics
        )
        self._judgement: asyncio.Task[None] | None = None
        self._judge_again = False
        self._ring = _Timer(self.post)
        self._silence = _Timer(self.post)
        self._lifetime = _Timer(self.post)
        self._findings = Findings()
        # The assistant handed the call over to a user not yet on it: its part ends when they join.
        self._handed_over = False
        self._finished = False
        self._owner: UserId | None = None
        self._ledger: CallLedger | None = None

    @property
    def call_id(self) -> CallId:
        return self._incoming.call_id

    @property
    def owner(self) -> UserId | None:
        """Whose call this is, once that has been found."""
        return self._owner

    @property
    def standing(self) -> CallStanding | None:
        """Where the call is, once it has an owner and so a record."""
        ledger = self._ledger
        if ledger is None:
            return None
        return CallStanding(self.call_id, ledger.state, ledger.state_since)

    @property
    def is_over(self) -> bool:
        """Whether the call has been torn down, or was never anybody's to hold."""
        return self._finished

    def post(self, item: Input) -> None:
        """Hand the run something to act on, after everything already handed to it."""
        self._inbox.put_nowait(item)

    async def run(self) -> None:
        """Act on the arrival, then on every input in turn, until the call is torn down.

        On every exit, cancellation included, nothing the run started is left running, and a
        request still waiting in the inbox is told the call is over.
        """
        bind_call(self.call_id.value, self._incoming.correlation_id)
        try:
            with self._context.tracer.span("call", **{CALL_ID: self.call_id.value}):
                live = await self._arrive()
                while live is not None and not self._finished:
                    await self._handle(live, await self._inbox.get())
        finally:
            await self._release_tasks()
            self._empty_inbox()

    # ------------------------------------------------------------------------ arrival and routing

    async def _arrive(self) -> _Live | None:
        owner = await self._find_owner()
        if owner is None:
            # Nobody's call: there is nobody to record it for, and nobody to put it through to.
            await self._terminate()
            self._finished = True
            self._context.metrics.increment(ROUTED, {"outcome": "nobody"})
            return None
        context = self._context
        # When the call arrived, where its transport said: a handset reports its calls afterwards.
        arrived = self._incoming.occurred_at
        # Asked and answered before anything is awaited, so two calls arriving together cannot both
        # take the last room. A call refused is given no owner: it holds none of the account's room.
        admitted = context.admits(owner.user_id)
        if admitted:
            self._owner = owner.user_id
        live = _Live(
            owner=owner,
            ledger=CallLedger(
                CallSession(
                    id=self.call_id,
                    user_id=owner.user_id,
                    caller=_recognised(self._incoming.caller or Caller(), owner.preferences),
                    started_at=reported_instant(arrived, now=context.clock.now()),
                ),
                stores=context.stores,
                clock=context.clock,
                bounds=context.bounds,
                metrics=context.metrics,
            ),
        )
        self._ledger = live.ledger
        for dependency in self._degraded:
            live.ledger.note(MarkKind.DEGRADED, dependency.value)
        await live.ledger.opened()
        if not admitted:
            logger.warning("call.too_many_live_calls")
            context.metrics.increment(CALL_BOUNDED, {"kind": "live_calls"})
            await self._finish(live, CallState.FAILED)
            return live
        self._lifetime.arm(context.bounds.duration, CallRanTooLong)
        await live.ledger.move(CallState.ROUTING, at=arrived)
        with context.tracer.span("call.routing") as span:
            posture = route(live.ledger.call.caller, owner.preferences, context.clock.now())
            decided = route_on(posture, self._plan)
            span.set_attribute("call.route", decided.value)
        context.metrics.increment(ROUTED, {"outcome": decided.value})
        match decided:
            case Route.ASSISTANT if self._plan.assistant is not None:
                await self._hand_to_assistant(live, self._plan.assistant)
            case Route.PASS_THROUGH:
                await self._put_through(live, arrived)
            case _:
                await self._finish(live, CallState.REJECTED, at=arrived)
        return live

    async def _find_owner(self) -> Owner | None:
        try:
            async with asyncio.timeout(self._context.bounds.storage.total_seconds()):
                user_id = await self._line.ownership.owner_of(self._incoming)
                if user_id is None:
                    return None
                async with self._context.stores() as stores:
                    return await _owner(stores, user_id)
        # Nobody can be found for a call while storage is down, and a call nobody owns is released
        # rather than held: logged and counted by kind, not raised past the run.
        except Exception as error:  # noqa: BLE001
            # Without a traceback: its frames can hold who called.
            log_failure(logger, "call.owner_unavailable", error)
            self._context.metrics.increment(
                PROVIDER_FAILED, {"stage": "owner", "kind": classify(error).kind}
            )
            return None

    async def _put_through(self, live: _Live, arrived: datetime | None) -> None:
        await live.ledger.move(CallState.PASSTHROUGH, at=arrived)
        step = self._plan.put_through
        if not isinstance(step, DialTheUser):
            # It rings where it is, and whoever holds that phone answers it.
            return
        if not await self._dial(live, step):
            await self._finish(live, CallState.FAILED)
            return
        self._ring_for(step)

    async def _hand_to_assistant(self, live: _Live, step: Converse) -> None:
        await live.ledger.move(CallState.AGENT_HANDLING)
        if not await self._provider("answer", lambda: step.answering.answer(self.call_id)):
            await self._finish(live, CallState.FAILED)
            return
        context = self._context
        stopwatch = Stopwatch()
        try:
            with context.tracer.span("speech.open", dependency=Dependency.SPEECH.value):
                await context.circuits[Dependency.SPEECH].call(
                    lambda: self._speaking.start(
                        step, live.owner.preferences, context.bounds.speech_open
                    )
                )
        # A speech service that will not open, or not in time, leaves nobody to talk to the caller:
        # the call fails, and the user reads that in its history.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.speech_unavailable", error)
            kind = classify(error).kind
            context.metrics.increment(PROVIDER_FAILED, {"stage": "speech", "kind": kind})
            live.ledger.note(MarkKind.FAILURE, f"speech.{kind}")
            context.metrics.observe(SPEECH_OPEN_SECONDS, stopwatch.seconds, {"outcome": "failed"})
            await self._finish(live, CallState.FAILED)
        else:
            context.metrics.observe(SPEECH_OPEN_SECONDS, stopwatch.seconds, {"outcome": "opened"})

    # ------------------------------------------------------------------------------ the inbox

    async def _handle(self, live: _Live, item: Input) -> None:
        match item:
            case Reported(event=event):
                if event.event_id in self._seen:
                    self._context.metrics.increment(DUPLICATE_IGNORED, {"stage": "repeated"})
                else:
                    self._seen.add(event.event_id)
                    await self._on_event(live, event)
            case Heard(turn=turn):
                await self._on_heard(live, turn)
            case ConversationStopped(end=end):
                await self._on_conversation_stopped(live, end)
            case Judged(judgement=judgement):
                self._on_judged(live, judgement)
            case RingRanOut(dial=dial, generation=generation) if self._ring.is_current(generation):
                await self._on_ring_ran_out(live, dial)
            case SilenceRanOut(generation=generation) if self._silence.is_current(generation):
                await self._assistant_lost(live)
            case CallRanTooLong(generation=generation) if self._lifetime.is_current(generation):
                await self._ran_too_long(live)
            case Abandoned():
                await self._finish(live, CallState.FAILED)
            case EscalationRequested() | EndingRequested() | OutcomeRecorded() | MessageTaken():
                await self._on_request(live, item)
            case _:
                # A wait that ran out after it was cancelled: its expiry was already on the way.
                pass

    async def _on_event(self, live: _Live, event: CallEvent) -> None:
        # What the event changes is recorded at the moment it happened, where its transport said.
        at = event.occurred_at
        match event.kind:
            case CallEventKind.ENDED:
                self._findings = replace(self._findings, caller_hung_up=True)
                await self._finish(live, CallState.COMPLETED, at=at)
            case CallEventKind.FAILED:
                await self._finish(live, CallState.FAILED, at=at)
            case CallEventKind.ANSWERED if isinstance(self._plan.put_through, LetItRing):
                # On a call that rings where it is, answering is the user picking it up. Elsewhere
                # it is the caller being answered into the call, which changes nothing here.
                await live.ledger.joined(ParticipantRole.HUMAN, at=at)
            case CallEventKind.PARTICIPANT_JOINED:
                await self._on_joined(live, event.participant, at)
            case CallEventKind.PARTICIPANT_UNREACHABLE:
                await self._on_unreachable(live, event.participant, event.outcome, at)
            case CallEventKind.PARTICIPANT_LEFT:
                await self._on_left(live, event.participant, at)
            case _:
                # The caller answered into the call, or the call announced again under another
                # identifier: nothing that was not already known.
                pass

    async def _on_joined(self, live: _Live, leg: Leg | None, at: datetime | None) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await ledger.joined(ParticipantRole.AGENT, at=at)
            return
        match ledger.state:
            case CallState.HUMAN_RINGING:
                self._ring.cancel()
                self._context.metrics.increment(
                    ESCALATION_RESOLVED, {"outcome": ParticipantOutcome.ANSWERED.value}
                )
                await ledger.move(CallState.HUMAN_JOINED, at=at)
                # The assistant was on the call before the user was rung, even when the callback
                # saying so is still on its way: record it first, so the order is the true one.
                if not any(each.role is ParticipantRole.AGENT for each in ledger.call.participants):
                    await ledger.joined(ParticipantRole.AGENT, at=at)
                await ledger.joined(ParticipantRole.HUMAN, at=at)
                if self._handed_over:
                    await self._speaking.stop()
                else:
                    await self._tell(Situation(UserReach.ON_THE_CALL))
            case CallState.PASSTHROUGH:
                self._ring.cancel()
                await ledger.joined(ParticipantRole.HUMAN, at=at)
            case _:
                # The user answering a ring already given up on, whose cancelling did not reach the
                # provider in time: they are on the call all the same. The assistant keeps it, and
                # is told they are there; the record says who was on it.
                await ledger.joined(ParticipantRole.HUMAN, at=at)
                await self._tell(Situation(UserReach.ON_THE_CALL))

    async def _on_unreachable(
        self,
        live: _Live,
        leg: Leg | None,
        outcome: ParticipantOutcome | None,
        at: datetime | None,
    ) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await self._assistant_lost(live, at)
            return
        match ledger.state:
            case CallState.HUMAN_RINGING:
                await self._not_reached(
                    live, outcome or ParticipantOutcome.FAILED, cancel=None, at=at
                )
            case CallState.PASSTHROUGH if not ledger.call.has_participant(ParticipantRole.HUMAN):
                await self._finish(live, CallState.COMPLETED, at=at)
            case _:
                pass

    async def _on_left(self, live: _Live, leg: Leg | None, at: datetime | None) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await ledger.left(ParticipantRole.AGENT, at=at)
            await self._speaking.stop()
            self._expect_hang_up()
            return
        await ledger.left(ParticipantRole.HUMAN, at=at)
        if ledger.state in {CallState.HUMAN_JOINED, CallState.PASSTHROUGH}:
            await self._finish(live, CallState.COMPLETED, at=at)

    async def _ran_too_long(self, live: _Live) -> None:
        """Nothing reported the call ending in all the time a call may last, so it is ended here."""
        limit = int(self._context.bounds.duration.total_seconds())
        logger.warning("call.ran_too_long", limit_seconds=limit)
        self._context.metrics.increment(CALL_BOUNDED, {"kind": "duration"})
        await self._finish(live, CallState.FAILED)

    def _expect_hang_up(self) -> None:
        """The assistant's audio went: the transport has `speaker_gone` to say the call ended."""
        self._silence.arm(self._context.bounds.speaker_gone, SilenceRanOut)

    async def _assistant_lost(self, live: _Live, at: datetime | None = None) -> None:
        """The assistant cannot go on. The call stands only while the user is coming or here."""
        await self._speaking.stop()
        if live.ledger.state is CallState.AGENT_HANDLING:
            await self._finish(live, CallState.FAILED, at=at)

    async def _on_conversation_stopped(self, live: _Live, end: ConversationEnd | None) -> None:
        if end is ConversationEnd.SPEAKER_GONE:
            self._expect_hang_up()
            return
        logger.warning("call.conversation_lost", failed=end is None)
        live.ledger.note(MarkKind.FAILURE, "conversation.lost")
        await self._assistant_lost(live)

    async def _on_heard(self, live: _Live, turn: TranscriptTurn) -> None:
        await live.ledger.said(_speaker(turn), turn.text)
        assistant = self._plan.assistant
        if turn.speaker_is_caller and live.ledger.state in _JUDGED_IN and assistant is not None:
            self._judge(live, assistant)

    async def _on_ring_ran_out(self, live: _Live, dial: DialTheUser) -> None:
        if live.ledger.state is CallState.HUMAN_RINGING:
            await self._not_reached(live, ParticipantOutcome.NO_ANSWER, cancel=dial)
            return
        # Put through, and nobody picked up.
        await self._cancel_dial(live, dial)
        await self._finish(live, CallState.COMPLETED)

    # ---------------------------------------------------------------------- the agent's requests

    async def _on_request(self, live: _Live, request: Request) -> None:
        try:
            match request:
                case EscalationRequested(decision=decision):
                    await self._escalate(live, decision)
                case EndingRequested(ending=ending, assessment=assessment):
                    # Kept before acting on it: ending the call cancels the judgement that asked,
                    # and with it the report of what it judged the call to be.
                    self._findings = replace(self._findings, proposal=assessment)
                    await self._end_for_agent(live, ending)
                case OutcomeRecorded(record=record):
                    self._findings = replace(self._findings, outcome=record)
                case _:
                    message = request.message
                    messages = (*self._findings.messages, message)
                    self._findings = replace(self._findings, messages=messages)
        except DomainError as error:
            _settle(request.reply, error)
        else:
            _settle(request.reply, None)

    async def _escalate(self, live: _Live, decision: EscalationDecision) -> None:
        ledger = live.ledger
        if ledger.state is not CallState.AGENT_HANDLING:
            # Already reaching the user, or reached: one ring at a time.
            return
        self._findings = replace(self._findings, escalation_reason=decision.reason)
        step = self._plan.escalation
        reason = decision.reason
        if step is None or reason is None or not decision.is_immediate:
            # Nothing rings: the plan has no way to add the user, or the rules said not now. The
            # reason is kept, and the user reads it in the call's history.
            return
        with self._context.tracer.span("call.escalation", outcome=reason.value):
            await ledger.move(CallState.ESCALATION_REQUESTED)
            # Started, never awaited, so delivering it never holds the dial back (D-016).
            self._notify(live, decision, reason)
            # The assistant learns the user is being reached as the dial starts, not after it.
            _, dialled = await asyncio.gather(
                self._tell(Situation(UserReach.BEING_REACHED)), self._dial(live, step)
            )
            if not dialled:
                self._context.metrics.increment(ESCALATION_RESOLVED, {"outcome": "dial_refused"})
                await ledger.move(CallState.AGENT_HANDLING)
                await self._tell(Situation(UserReach.NOT_REACHED, ParticipantOutcome.FAILED))
                raise DialRefusedError
            await ledger.move(CallState.HUMAN_RINGING)
            self._ring_for(step)

    async def _end_for_agent(self, live: _Live, ending: CallEnding) -> None:
        handed_over = ending is CallEnding.HANDED_OVER
        if handed_over and live.ledger.state is CallState.HUMAN_JOINED:
            # Handed over to a user who is here: the assistant's part is done, and the call is not
            # the assistant's to hang up on them.
            await self._speaking.stop()
            return
        if handed_over and live.ledger.state in _USER_BEING_REACHED:
            # Handed over to a user still being reached. Until they answer, the assistant keeps the
            # caller company, and takes the call back if they do not: stopping it now would leave
            # the caller in silence, and the call ended by nobody's choice when the user is busy.
            self._handed_over = True
            return
        # The agent usually asks while the assistant is still saying goodbye. Hung up at once, the
        # caller hears it cut off mid-sentence and its last words never reach the transcript.
        bounds = self._context.bounds
        await self._speaking.finish_speaking(bounds.goodbye, bounds.goodbye_pause)
        await self._finish(live, CallState.COMPLETED)

    def _notify(self, live: _Live, decision: EscalationDecision, reason: EscalationReason) -> None:
        call = live.ledger.call
        if len(call.id.value) > MAX_CALL_ID_LENGTH:
            # A notification that cannot be bound to its call is not sent; the ring still is.
            logger.warning("call.escalation_not_notified")
            return
        number = call.caller.number
        contact = None if number is None else live.owner.preferences.contact_for(number)
        summary = decision.caller_summary
        self._context.dispatcher.start(
            live.owner.user_id,
            EscalationContext(
                call_id=call.id,
                reason=reason,
                raised_at=self._context.clock.now(),
                caller_label=None if contact is None else contact.label,
                established=None if summary is None else summary[:MAX_DETAIL_LENGTH],
            ),
            locale=live.owner.preferences.locale,
        )

    async def _not_reached(
        self,
        live: _Live,
        outcome: ParticipantOutcome,
        *,
        cancel: DialTheUser | None,
        at: datetime | None = None,
    ) -> None:
        """The user did not come: the assistant takes the call back, and is told why.

        `cancel` is the dial still ringing them, when it is this side that gave up on it; `at` is
        when the transport said they did not come.
        """
        self._ring.cancel()
        self._handed_over = False
        self._context.metrics.increment(ESCALATION_RESOLVED, {"outcome": outcome.value})
        if cancel is not None:
            await self._cancel_dial(live, cancel)
        if not self._speaking.is_speaking:
            # Nobody is left to take it back.
            await self._finish(live, CallState.COMPLETED, at=at)
            return
        await live.ledger.move(CallState.AGENT_HANDLING, at=at)
        await self._tell(Situation(UserReach.NOT_REACHED, outcome))

    async def _cancel_dial(self, live: _Live, dial: DialTheUser) -> None:
        await self._provider(
            "cancel", lambda: dial.bridge.remove_participant(self.call_id, live.owner.number)
        )

    # ------------------------------------------------------------------------------- judgement

    def _judge(self, live: _Live, assistant: Converse) -> None:
        if self._judgement is not None and not self._judgement.done():
            self._judge_again = True
            return
        call = live.ledger.call
        so_far = CallSoFar.for_user(
            call_id=call.id,
            preferences=live.owner.preferences,
            caller=call.caller,
            transcript=call.transcript,
            now=self._context.clock.now(),
        )
        self._judgement = asyncio.get_running_loop().create_task(self._look(assistant, so_far))

    async def _look(self, assistant: Converse, so_far: CallSoFar) -> None:
        context = self._context
        agent = assistant.assistance.judging.agent
        stopwatch = Stopwatch()
        try:
            with context.tracer.span("agent.judgement", dependency=Dependency.MODEL.value):
                judgement = await context.circuits[Dependency.MODEL].call(
                    lambda: within(context.bounds.judgement, lambda: agent.judge(so_far))
                )
        # A judgement that fails or runs out of time changes nothing about the call: the assistant
        # carries on, and the next thing the caller says is looked at afresh.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.judgement_failed", error)
            kind = classify(error).kind
            context.metrics.increment(JUDGEMENT_FAILED, {"kind": kind})
            self._note(MarkKind.FAILURE, f"judgement.{kind}")
            context.metrics.observe(JUDGEMENT_SECONDS, stopwatch.seconds, {"outcome": "failed"})
            self.post(Judged(None))
        else:
            context.metrics.observe(JUDGEMENT_SECONDS, stopwatch.seconds, {"outcome": "judged"})
            self.post(Judged(judgement))

    def _on_judged(self, live: _Live, judgement: AgentJudgement | None) -> None:
        if judgement is not None:
            self._findings = replace(self._findings, proposal=judgement.proposal)
        assistant = self._plan.assistant
        if self._judge_again and live.ledger.state in _JUDGED_IN and assistant is not None:
            self._judge_again = False
            self._judge(live, assistant)

    # -------------------------------------------------------------------------------- teardown

    async def _finish(self, live: _Live, state: CallState, *, at: datetime | None = None) -> None:
        """The one teardown. Every ending of a call reaches it, and it runs once.

        `at` is when the event that ended the call happened, where its transport said.

        The conversation, the speech session, the judgement and the timers stop; the transport lets
        the call go; the agent forgets it; the call is stored as it ended with its summary; and the
        user's escalation context, if there is one, is marked ended.

        The summary is written after the transport has let the call go, so however long it takes
        nobody is left on a line waiting for it, and that wait has a bound of its own.
        """
        self._finished = True
        ledger = live.ledger
        if ledger.state in _USER_BEING_REACHED:
            self._context.metrics.increment(ESCALATION_RESOLVED, {"outcome": "call_ended"})
        with self._context.tracer.span("call.teardown", **{"call.state": state.value}):
            await self._release_tasks()
            for turn in self._empty_inbox():
                await ledger.said(_speaker(turn), turn.text)
            await ledger.move(state, at=at)
            await self._terminate()
            if self._plan.assistant is not None:
                self._plan.assistant.assistance.judging.forget(self.call_id)
            written = await self._summary(live, facts_of(ledger.call, self._findings))
            summary = with_findings(written, ledger.call, self._findings)
            await ledger.summarised(summary)
            await self._context.dispatcher.call_ended(
                live.owner.user_id, self.call_id, summary.ended_at
            )
        self._context.metrics.increment(CALL_ENDED, {"outcome": state.value})

    async def _summary(self, live: _Live, facts: CallFacts) -> CallSummary:
        """The summariser's summary of a call the assistant took; the facts' own of any other.

        A call nobody spoke with has nothing a model could read, and one the summariser cannot
        write in time is summarised from its facts, which is what the summariser itself does with
        a model that is down or slow.
        """
        call = live.ledger.call
        locale = live.owner.preferences.locale
        context = self._context
        summariser = context.summariser
        if summariser is None or call.handling is not CallHandling.ASSISTANT:
            return fallback_summary(facts, locale=locale)
        if context.circuits[Dependency.MODEL].is_refusing:
            # The model is failing every call: asking it would only wait out the bound first.
            context.metrics.increment(DEGRADED, {"stage": "summary"})
            live.ledger.note(MarkKind.DEGRADED, "summary")
            return fallback_summary(facts, locale=locale)
        stopwatch = Stopwatch()
        try:
            with context.tracer.span("summary.write", dependency=Dependency.MODEL.value):
                written = await within(
                    context.bounds.summary,
                    lambda: summariser.summarise(facts, call.transcript, locale=locale),
                )
        except TimeoutError:
            logger.warning("call.summary_failed", error="TimeoutError")
            context.metrics.increment(SUMMARY_FAILED, {"kind": FailureKind.TIMEOUT})
            live.ledger.note(MarkKind.FAILURE, f"summary.{FailureKind.TIMEOUT}")
            context.metrics.observe(SUMMARY_SECONDS, stopwatch.seconds, {"outcome": "fallback"})
            return fallback_summary(facts, locale=locale)
        context.metrics.observe(SUMMARY_SECONDS, stopwatch.seconds, {"outcome": "written"})
        return written

    async def _release_tasks(self) -> None:
        """Stop the judgement, the timers and the conversation, and wait for each to go."""
        judgement, self._judgement = self._judgement, None
        if judgement is not None:
            judgement.cancel()
            await asyncio.gather(judgement, return_exceptions=True)
        await self._ring.release()
        await self._silence.release()
        await self._lifetime.release()
        await self._speaking.stop()

    def _empty_inbox(self) -> list[TranscriptTurn]:
        """Empty the inbox, refusing waiting requests, and return the lines not yet recorded."""
        heard: list[TranscriptTurn] = []
        while not self._inbox.empty():
            match self._inbox.get_nowait():
                case Heard(turn=turn):
                    heard.append(turn)
                case (
                    EscalationRequested()
                    | EndingRequested()
                    | OutcomeRecorded()
                    | MessageTaken() as request
                ):
                    _settle(request.reply, CallIsOverError())
                case _:
                    pass
        return heard

    # ------------------------------------------------------------------------------- utilities

    async def _dial(self, live: _Live, step: DialTheUser) -> bool:
        # Never retried: a second dial is a second ring on the user's phone.
        return await self._provider(
            "dial", lambda: step.bridge.add_participant(self.call_id, live.owner.number)
        )

    async def _terminate(self) -> bool:
        return await self._provider(
            "terminate", lambda: self._line.transport.terminate(self.call_id), repeatable=True
        )

    async def _provider(
        self, stage: str, work: Callable[[], Awaitable[None]], *, repeatable: bool = False
    ) -> bool:
        """Ask the transport for something, within the provider bound, and say whether it did.

        Through the transport's circuit, so a transport failing every call is not waited on by each.
        `repeatable` work, safe to ask twice, is asked again after a failure that may pass.
        """
        context = self._context
        circuit = context.circuits[Dependency.TELEPHONY]

        async def attempt() -> None:
            await circuit.call(lambda: within(context.bounds.provider, work))

        stopwatch = Stopwatch()
        try:
            with context.tracer.span(f"telephony.{stage}", dependency=Dependency.TELEPHONY.value):
                if repeatable:
                    await retry_idempotent(attempt, policy=TERMINATE_RETRY)
                else:
                    await attempt()
        except (TimeoutError, DomainError) as error:
            failure = classify(error)
            log_failure(logger, "call.provider_failed", error, stage=stage)
            context.metrics.increment(PROVIDER_FAILED, {"stage": stage, "kind": failure.kind})
            self._note(MarkKind.FAILURE, f"{stage}.{failure.kind}")
            if failure.kind is not FailureKind.CIRCUIT_OPEN:
                context.metrics.observe(PROVIDER_SECONDS, stopwatch.seconds, {"stage": stage})
            return False
        context.metrics.observe(PROVIDER_SECONDS, stopwatch.seconds, {"stage": stage})
        return True

    def _note(self, kind: MarkKind, name: str) -> None:
        # A call nobody owns has no record, and so no timeline to mark.
        if self._ledger is not None:
            self._ledger.note(kind, name)

    async def _tell(self, situation: Situation) -> None:
        await self._speaking.tell(situation, self._context.bounds.provider)

    def _ring_for(self, dial: DialTheUser) -> None:
        self._ring.arm(self._context.bounds.ring, lambda generation: RingRanOut(dial, generation))


class _Timer:
    """One wait a run arms, cancels and releases. What it posts says which arming ran out."""

    def __init__(self, post: Callable[[Input], None]) -> None:
        self._post = post
        self._task: asyncio.Task[None] | None = None
        self._generation = 0

    def arm(self, after: timedelta, expired: Callable[[int], Input]) -> None:
        self.cancel()
        self._generation += 1
        generation = self._generation

        async def wait() -> None:
            await asyncio.sleep(after.total_seconds())
            self._post(expired(generation))

        self._task = asyncio.get_running_loop().create_task(wait())

    def cancel(self) -> None:
        """Disarm. An expiry already posted is recognised as stale by its generation."""
        self._generation += 1
        if self._task is not None:
            self._task.cancel()

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    async def release(self) -> None:
        self.cancel()
        task, self._task = self._task, None
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class _Live:
    """A call that has an owner, and so a record."""

    owner: Owner
    ledger: CallLedger


async def _owner(stores: CallStores, user_id: UserId) -> Owner | None:
    user = await stores.users.get(user_id)
    if user is None:
        return None
    preferences = await stores.preferences.get(user_id)
    return Owner(user_id, user.phone_number, preferences or UserPreferences())


def _recognised(caller: Caller, preferences: UserPreferences) -> Caller:
    """The caller as the user knows them: under their own label, when they named the number.

    The same precedence routing gives an important contact over any category, so a call routed as
    somebody the user named is recorded, listed and summarised as them, not as a stranger.
    """
    contact = None if caller.number is None else preferences.contact_for(caller.number)
    if contact is None:
        return caller
    return replace(caller, display_name=contact.label, category=CallerCategory.KNOWN_CONTACT)


def _speaker(turn: TranscriptTurn) -> Speaker:
    return Speaker.CALLER if turn.speaker_is_caller else Speaker.AGENT


def _settle(reply: asyncio.Future[None], error: DomainError | None) -> None:
    # A request whose asker has gone — a judgement cancelled while it waited — has nobody to hear
    # the answer, and an exception set on it would be reported as never retrieved.
    if reply.done():
        return
    if error is None:
        reply.set_result(None)
    else:
        reply.set_exception(error)
