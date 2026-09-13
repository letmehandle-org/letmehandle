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
                   → FAILED when the transport or the assistant fails, or the process stops

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
from letmehandle.application.orchestration.ledger import CallLedger
from letmehandle.application.orchestration.plan import DialTheUser, LetItRing
from letmehandle.application.orchestration.routing import Route, route_on
from letmehandle.application.orchestration.speaking import Situation, Speaking, UserReach
from letmehandle.application.orchestration.summary import Findings, facts_of, with_findings
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
from letmehandle.domain.policy.routing import route
from letmehandle.domain.ports.call_transport import CallEventKind, ParticipantOutcome
from letmehandle.domain.ports.call_transport import ParticipantRole as Leg
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import timedelta

    from letmehandle.application.agent.ports import AgentJudgement
    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.application.escalation.dispatch import EscalationDispatcher
    from letmehandle.application.orchestration.inputs import Input, Request
    from letmehandle.application.orchestration.plan import CallPlan, Converse
    from letmehandle.application.orchestration.ports import (
        Bounds,
        CallOwnership,
        CallStores,
        OpenCallStores,
    )
    from letmehandle.domain.models.escalation import EscalationDecision, EscalationReason
    from letmehandle.domain.models.identifiers import CallId, EventId, UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.call_transport import CallEvent, CallTransport
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder

logger = get_logger(__name__)

PROVIDER_FAILED: Final = catalogue.count(
    "call.provider_failed",
    stage={"owner", "speech", "answer", "dial", "cancel", "terminate"},
    kind=FailureKind,
)
JUDGEMENT_FAILED: Final = catalogue.count("call.judgement_failed", kind=FailureKind)
SUMMARY_FAILED: Final = catalogue.count("call.summary_failed", kind=FailureKind)
CALL_ENDED: Final = catalogue.count("call.ended", outcome=TERMINAL)

# While the assistant is on the call and the user is not, something the caller says is worth
# another look. Once the user has joined, the call is theirs to handle.
_JUDGED_IN: Final = frozenset(
    {CallState.AGENT_HANDLING, CallState.ESCALATION_REQUESTED, CallState.HUMAN_RINGING}
)

_REQUESTS: Final = (EscalationRequested, EndingRequested, OutcomeRecorded, MessageTaken)

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

    transport: CallTransport
    ownership: CallOwnership
    stores: OpenCallStores
    dispatcher: EscalationDispatcher
    clock: Clock
    metrics: MetricsRecorder
    bounds: Bounds
    summariser: CallSummariser | None


@dataclass(frozen=True, slots=True)
class Owner:
    """The user a call is for, as far as a run needs them."""

    user_id: UserId
    number: PhoneNumber
    preferences: UserPreferences


class CallRun:
    """One call's inbox, state and teardown."""

    def __init__(self, incoming: CallEvent, plan: CallPlan, context: RunContext) -> None:
        self._incoming = incoming
        self._plan = plan
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
        self._findings = Findings()
        # The assistant handed the call over to a user not yet on it: its part ends when they join.
        self._handed_over = False
        self._finished = False
        self._owner: UserId | None = None

    @property
    def call_id(self) -> CallId:
        return self._incoming.call_id

    @property
    def owner(self) -> UserId | None:
        """Whose call this is, once that has been found."""
        return self._owner

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
        try:
            live = await self._arrive()
            while live is not None and not self._finished:
                await self._handle(live, await self._inbox.get())
        finally:
            await self._release_tasks()
            self._refuse_waiting()

    # ------------------------------------------------------------------------ arrival and routing

    async def _arrive(self) -> _Live | None:
        owner = await self._find_owner()
        if owner is None:
            # Nobody's call: there is nobody to record it for, and nobody to put it through to.
            await self._provider("terminate", self._context.transport.terminate(self.call_id))
            self._finished = True
            return None
        self._owner = owner.user_id
        context = self._context
        live = _Live(
            owner=owner,
            ledger=CallLedger(
                CallSession(
                    id=self.call_id,
                    user_id=owner.user_id,
                    caller=_recognised(self._incoming.caller or Caller(), owner.preferences),
                    started_at=context.clock.now(),
                ),
                stores=context.stores,
                clock=context.clock,
                bounds=context.bounds,
                metrics=context.metrics,
            ),
        )
        await live.ledger.opened()
        await live.ledger.move(CallState.ROUTING)
        posture = route(live.ledger.call.caller, owner.preferences, context.clock.now())
        match route_on(posture, self._plan):
            case Route.ASSISTANT if self._plan.assistant is not None:
                await self._hand_to_assistant(live, self._plan.assistant)
            case Route.PASS_THROUGH:
                await self._put_through(live)
            case _:
                await self._finish(live, CallState.REJECTED)
        return live

    async def _find_owner(self) -> Owner | None:
        try:
            async with asyncio.timeout(self._context.bounds.storage.total_seconds()):
                user_id = await self._context.ownership.owner_of(self._incoming)
                if user_id is None:
                    return None
                async with self._context.stores() as stores:
                    return await _owner(stores, user_id)
        # Nobody can be found for a call while storage is down, and a call nobody owns is released
        # rather than held: logged and counted by kind, not raised past the run.
        except Exception as error:  # noqa: BLE001
            # Without a traceback: its frames can hold who called.
            logger.error("call.owner_unavailable", error=type(error).__name__)  # noqa: TRY400
            self._context.metrics.increment(
                PROVIDER_FAILED, {"stage": "owner", "kind": classify(error).kind}
            )
            return None

    async def _put_through(self, live: _Live) -> None:
        await live.ledger.move(CallState.PASSTHROUGH)
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
        if not await self._provider("answer", step.answering.answer(self.call_id)):
            await self._finish(live, CallState.FAILED)
            return
        try:
            await self._speaking.start(
                step, live.owner.preferences, self._context.bounds.speech_open
            )
        # A speech service that will not open, or not in time, leaves nobody to talk to the caller:
        # the call fails, and the user reads that in its history.
        except Exception as error:  # noqa: BLE001
            logger.error("call.speech_unavailable", error=type(error).__name__)  # noqa: TRY400
            self._context.metrics.increment(
                PROVIDER_FAILED, {"stage": "speech", "kind": classify(error).kind}
            )
            await self._finish(live, CallState.FAILED)

    # ------------------------------------------------------------------------------ the inbox

    async def _handle(self, live: _Live, item: Input) -> None:
        match item:
            case Reported(event=event):
                if event.event_id not in self._seen:
                    self._seen.add(event.event_id)
                    await self._on_event(live, event)
            case Heard(turn=turn):
                await self._on_heard(live, turn.text, by_caller=turn.speaker_is_caller)
            case ConversationStopped(end=end):
                await self._on_conversation_stopped(live, end)
            case Judged(judgement=judgement):
                self._on_judged(live, judgement)
            case RingRanOut(dial=dial, generation=generation) if self._ring.is_current(generation):
                await self._on_ring_ran_out(live, dial)
            case SilenceRanOut(generation=generation) if self._silence.is_current(generation):
                await self._assistant_lost(live)
            case Abandoned():
                await self._finish(live, CallState.FAILED)
            case EscalationRequested() | EndingRequested() | OutcomeRecorded() | MessageTaken():
                await self._on_request(live, item)
            case _:
                # A wait that ran out after it was cancelled: its expiry was already on the way.
                pass

    async def _on_event(self, live: _Live, event: CallEvent) -> None:
        match event.kind:
            case CallEventKind.ENDED:
                self._findings = replace(self._findings, caller_hung_up=True)
                await self._finish(live, CallState.COMPLETED)
            case CallEventKind.FAILED:
                await self._finish(live, CallState.FAILED)
            case CallEventKind.ANSWERED if isinstance(self._plan.put_through, LetItRing):
                # On a call that rings where it is, answering is the user picking it up. Elsewhere
                # it is the caller being answered into the call, which changes nothing here.
                await live.ledger.joined(ParticipantRole.HUMAN)
            case CallEventKind.PARTICIPANT_JOINED:
                await self._on_joined(live, event.participant)
            case CallEventKind.PARTICIPANT_UNREACHABLE:
                await self._on_unreachable(live, event.participant, event.outcome)
            case CallEventKind.PARTICIPANT_LEFT:
                await self._on_left(live, event.participant)
            case _:
                # The caller answered into the call, or the call announced again under another
                # identifier: nothing that was not already known.
                pass

    async def _on_joined(self, live: _Live, leg: Leg | None) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await ledger.joined(ParticipantRole.AGENT)
            return
        match ledger.state:
            case CallState.HUMAN_RINGING:
                self._ring.cancel()
                await ledger.move(CallState.HUMAN_JOINED)
                await ledger.joined(ParticipantRole.HUMAN)
                if self._handed_over:
                    await self._speaking.stop()
                else:
                    await self._tell(Situation(UserReach.ON_THE_CALL))
            case CallState.PASSTHROUGH:
                self._ring.cancel()
                await ledger.joined(ParticipantRole.HUMAN)
            case _:
                # The user answering a ring already given up on, whose cancelling did not reach the
                # provider in time: they are on the call all the same. The assistant keeps it, and
                # is told they are there; the record says who was on it.
                await ledger.joined(ParticipantRole.HUMAN)
                await self._tell(Situation(UserReach.ON_THE_CALL))

    async def _on_unreachable(
        self, live: _Live, leg: Leg | None, outcome: ParticipantOutcome | None
    ) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await self._assistant_lost(live)
            return
        match ledger.state:
            case CallState.HUMAN_RINGING:
                await self._not_reached(live, outcome or ParticipantOutcome.FAILED, cancel=None)
            case CallState.PASSTHROUGH if not ledger.call.has_participant(ParticipantRole.HUMAN):
                await self._finish(live, CallState.COMPLETED)
            case _:
                pass

    async def _on_left(self, live: _Live, leg: Leg | None) -> None:
        ledger = live.ledger
        if leg is Leg.ASSISTANT:
            await ledger.left(ParticipantRole.AGENT)
            await self._assistant_lost(live)
            return
        await ledger.left(ParticipantRole.HUMAN)
        if ledger.state in {CallState.HUMAN_JOINED, CallState.PASSTHROUGH}:
            await self._finish(live, CallState.COMPLETED)

    async def _assistant_lost(self, live: _Live) -> None:
        """The assistant cannot go on. The call stands only while the user is coming or here."""
        await self._speaking.stop()
        if live.ledger.state is CallState.AGENT_HANDLING:
            await self._finish(live, CallState.FAILED)

    async def _on_conversation_stopped(self, live: _Live, end: ConversationEnd | None) -> None:
        if end is ConversationEnd.SPEAKER_GONE:
            # Usually the caller hanging up, which the transport is about to say. If it does not
            # say so in time, the call's audio went for another reason, and the assistant with it.
            self._silence.arm(self._context.bounds.speaker_gone, SilenceRanOut)
            return
        logger.warning("call.conversation_lost", failed=end is None)
        await self._assistant_lost(live)

    async def _on_heard(self, live: _Live, text: str, *, by_caller: bool) -> None:
        await live.ledger.said(Speaker.CALLER if by_caller else Speaker.AGENT, text)
        assistant = self._plan.assistant
        if by_caller and live.ledger.state in _JUDGED_IN and assistant is not None:
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
        await ledger.move(CallState.ESCALATION_REQUESTED)
        # Started, never awaited: the ring is the escalation and the notification only context for
        # it, so nothing about delivering it may hold the dial back (D-016).
        self._notify(live, decision, reason)
        if not await self._dial(live, step):
            await ledger.move(CallState.AGENT_HANDLING)
            await self._tell(Situation(UserReach.NOT_REACHED, ParticipantOutcome.FAILED))
            raise DialRefusedError
        await ledger.move(CallState.HUMAN_RINGING)
        self._ring_for(step)
        await self._tell(Situation(UserReach.BEING_REACHED))

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
        )

    async def _not_reached(
        self, live: _Live, outcome: ParticipantOutcome, *, cancel: DialTheUser | None
    ) -> None:
        """The user did not come: the assistant takes the call back, and is told why.

        `cancel` is the dial still ringing them, when it is this side that gave up on it.
        """
        self._ring.cancel()
        self._handed_over = False
        if cancel is not None:
            await self._cancel_dial(live, cancel)
        if not self._speaking.is_speaking:
            # Nobody is left to take it back.
            await self._finish(live, CallState.COMPLETED)
            return
        await live.ledger.move(CallState.AGENT_HANDLING)
        await self._tell(Situation(UserReach.NOT_REACHED, outcome))

    async def _cancel_dial(self, live: _Live, dial: DialTheUser) -> None:
        await self._provider(
            "cancel", dial.bridge.remove_participant(self.call_id, live.owner.number)
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
        try:
            async with asyncio.timeout(self._context.bounds.judgement.total_seconds()):
                judgement = await assistant.assistance.judging.agent.judge(so_far)
        # A judgement that fails or runs out of time changes nothing about the call: the assistant
        # carries on, and the next thing the caller says is looked at afresh.
        except Exception as error:  # noqa: BLE001
            logger.warning("call.judgement_failed", error=type(error).__name__)
            self._context.metrics.increment(JUDGEMENT_FAILED, {"kind": classify(error).kind})
            self.post(Judged(None))
        else:
            self.post(Judged(judgement))

    def _on_judged(self, live: _Live, judgement: AgentJudgement | None) -> None:
        if judgement is not None:
            self._findings = replace(self._findings, proposal=judgement.proposal)
        assistant = self._plan.assistant
        if self._judge_again and live.ledger.state in _JUDGED_IN and assistant is not None:
            self._judge_again = False
            self._judge(live, assistant)

    # -------------------------------------------------------------------------------- teardown

    async def _finish(self, live: _Live, state: CallState) -> None:
        """The one teardown. Every ending of a call reaches it, and it runs once.

        The conversation, the speech session, the judgement and the timers stop; the transport lets
        the call go; the agent forgets it; the call is stored as it ended with its summary; and the
        user's escalation context, if there is one, is marked ended.

        The summary is written after the transport has let the call go, so however long it takes
        nobody is left on a line waiting for it, and that wait has a bound of its own.
        """
        self._finished = True
        ledger = live.ledger
        await ledger.move(state)
        await self._release_tasks()
        await self._provider("terminate", self._context.transport.terminate(self.call_id))
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
        summariser = self._context.summariser
        if summariser is None or call.handling is not CallHandling.ASSISTANT:
            return fallback_summary(facts, locale=locale)
        try:
            async with asyncio.timeout(self._context.bounds.summary.total_seconds()):
                return await summariser.summarise(facts, call.transcript, locale=locale)
        except TimeoutError:
            logger.warning("call.summary_failed", error="TimeoutError")
            self._context.metrics.increment(SUMMARY_FAILED, {"kind": FailureKind.TIMEOUT})
            return fallback_summary(facts, locale=locale)

    async def _release_tasks(self) -> None:
        """Stop the judgement, the timers and the conversation, and wait for each to go."""
        judgement, self._judgement = self._judgement, None
        if judgement is not None:
            judgement.cancel()
            await asyncio.gather(judgement, return_exceptions=True)
        await self._ring.release()
        await self._silence.release()
        await self._speaking.stop()

    def _refuse_waiting(self) -> None:
        while not self._inbox.empty():
            item = self._inbox.get_nowait()
            if isinstance(item, _REQUESTS):
                _settle(item.reply, CallIsOverError())

    # ------------------------------------------------------------------------------- utilities

    async def _dial(self, live: _Live, step: DialTheUser) -> bool:
        return await self._provider(
            "dial", step.bridge.add_participant(self.call_id, live.owner.number)
        )

    async def _provider(self, stage: str, work: Awaitable[None]) -> bool:
        """Ask the transport for something, within the provider bound, and say whether it did."""
        try:
            async with asyncio.timeout(self._context.bounds.provider.total_seconds()):
                await work
        except (TimeoutError, DomainError) as error:
            logger.warning("call.provider_failed", stage=stage, error=type(error).__name__)
            self._context.metrics.increment(
                PROVIDER_FAILED, {"stage": stage, "kind": classify(error).kind}
            )
            return False
        return True

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


def _settle(reply: asyncio.Future[None], error: DomainError | None) -> None:
    # A request whose asker has gone — a judgement cancelled while it waited — has nobody to hear
    # the answer, and an exception set on it would be reported as never retrieved.
    if reply.done():
        return
    if error is None:
        reply.set_result(None)
    else:
        reply.set_exception(error)
