"""Everything an orchestrator runs against, in memory, and a way to run one.

The lines are transports a test drives: it says what the provider reports, and reads back what the
orchestrator asked of it. They keep the port's promises — terminate is safe twice, an undeclared
capability has no method to call — and can be told to refuse or to hang on any request, which is how
a partial provider failure and a bounded wait are both put in front of a run.

The agent is scripted rather than modelled, but its escalations and endings go through the real
escalation service and the real conclusion, so the rules deciding whether a phone rings and whether
a hang-up is allowed are the product's own in every test.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.conclusion import JudgementConclusion
from letmehandle.application.agent.escalation import EscalationService
from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import AgentJudgement, CallAgent
from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.calls.summariser import CallSummariser
from letmehandle.application.escalation.dispatch import EscalationDispatcher
from letmehandle.application.orchestration.orchestrator import CallOrchestrator
from letmehandle.application.orchestration.ports import (
    Assistance,
    AssistantServices,
    Bounds,
    CallJudging,
    CallOwnership,
    CallStores,
)
from letmehandle.application.resilience.circuit import CircuitPolicy, Circuits
from letmehandle.domain.errors import AlreadyRecordedError, ProviderError, RecordNotFoundError
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, AudioFormat, AudioFrame
from letmehandle.domain.models.call import CallSession
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.domain.ports.audio_io import AudioSink, AudioSource
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallEvent,
    CallEventKind,
    CallTransport,
    ParticipantOutcome,
    ParticipantRole,
    ScreeningDecision,
    TransportCapabilities,
)
from letmehandle.domain.ports.notification import (
    DeliveryOutcome,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from letmehandle.domain.ports.repositories import (
    CallPage,
    CallRepository,
    SummaryRepository,
    TranscriptRepository,
    TranscriptStatus,
    check_page_size,
)
from letmehandle.domain.ports.speech import TranscriptProduced
from tests.contracts.auth_fakes import InMemoryUserRepository
from tests.contracts.fakes import (
    EchoSpeechProvider,
    EchoSpeechSession,
    FixedClock,
    RecordingNotificationProvider,
    StaticVoiceProvider,
)
from tests.contracts.preference_fakes import InMemoryPreferencesRepository
from tests.support.escalation_stores import InMemoryStores
from tests.support.recording_call_actions import RecordingCallActions
from tests.support.recording_metrics import RecordingMetrics
from tests.support.recording_tracer import RecordingTracer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence

    from letmehandle.application.agent.ports import (
        CallActions,
        CallEnding,
        CallSoFar,
        OutcomeRecord,
    )
    from letmehandle.application.calls.fallback import CallFacts
    from letmehandle.domain.models.call import TranscriptEntry
    from letmehandle.domain.models.call_state import CallState
    from letmehandle.domain.models.caller import Caller
    from letmehandle.domain.models.preferences import UserPreferences
    from letmehandle.domain.models.summary import CallSummary
    from letmehandle.domain.ports.repositories import CallCursor, CallFilter
    from letmehandle.domain.ports.speech import SpeechSession

OWNER: Final = UserId("user-1")
OWNERS_NUMBER: Final = PhoneNumber("+12025550143")
ANOTHER_OWNER: Final = UserId("user-2")
DEVICE: Final = DeviceToken(DevicePlatform.IOS, "phone-token")

# Short enough that a test waiting on one is quick, long enough that nothing else in a test runs
# into it by accident.
QUICK: Final = Bounds(
    ring=timedelta(seconds=0.3),
    judgement=timedelta(seconds=0.3),
    speech_open=timedelta(seconds=0.3),
    provider=timedelta(seconds=0.3),
    storage=timedelta(seconds=0.3),
    speaker_gone=timedelta(seconds=0.2),
    summary=timedelta(seconds=0.3),
    shutdown=timedelta(seconds=1),
)

ROUTINE: Final = EscalationProposal(importance=CallImportance.ROUTINE, intent=CallIntent.ENQUIRY)
WANTS_THE_USER: Final = EscalationProposal(
    importance=CallImportance.URGENT,
    intent=CallIntent.PERSONAL,
    caller_asked_for_the_user=True,
    caller_summary="Asked for the user by name.",
)


# ---------------------------------------------------------------------------------- storage


def snapshot(call: CallSession) -> CallSession:
    """A copy of the call as it stands, as storage would hold it: mutating one leaves the other."""
    return CallSession.restore(
        id=call.id,
        user_id=call.user_id,
        caller=call.caller,
        started_at=call.started_at,
        state=call.state,
        participants=call.participants,
        ended_at=call.ended_at,
        handling=call.handling,
        escalated_at=call.escalated_at,
    )


@dataclass
class MemoryCalls(CallRepository):
    stored: dict[CallId, CallSession] = field(default_factory=dict)
    states: dict[CallId, list[CallState]] = field(default_factory=lambda: defaultdict(list))
    # Reads answer and writes are refused, as a database gone read-only does.
    refusing_writes: bool = False
    # How many writes from now are refused before they answer again, as a connection reset does.
    refusing_next: int = 0

    async def save(self, call: CallSession) -> None:
        if self.refusing_writes:
            raise ConnectionError("the database is read-only")
        if self.refusing_next:
            self.refusing_next -= 1
            raise ConnectionError("the connection was reset")
        existing = self.stored.get(call.id)
        if existing is not None and existing.user_id != call.user_id:
            raise RecordNotFoundError("call", call.id.value)
        if (
            existing is not None
            and existing.is_over
            and (existing.state, existing.ended_at) != (call.state, call.ended_at)
        ):
            raise AlreadyRecordedError("call", call.id.value)
        self.stored[call.id] = snapshot(call)
        history = self.states[call.id]
        if not history or history[-1] is not call.state:
            history.append(call.state)

    async def get(self, user_id: UserId, call_id: CallId) -> CallSession | None:
        call = self.stored.get(call_id)
        return None if call is None or call.user_id != user_id else snapshot(call)

    async def list_for_user(
        self,
        user_id: UserId,
        *,
        limit: int,
        after: CallCursor | None = None,
        matching: CallFilter | None = None,
    ) -> CallPage:
        raise NotImplementedError("history is not what orchestration reads")

    async def unfinished(self, *, limit: int) -> tuple[CallSession, ...]:
        check_page_size(limit, 100)
        open_calls = sorted(
            (call for call in self.stored.values() if not call.is_over),
            key=lambda call: (call.started_at, call.id.value),
        )
        return tuple(snapshot(call) for call in open_calls[:limit])

    async def delete(self, user_id: UserId, call_id: CallId) -> None:
        raise NotImplementedError("deleting is not what orchestration does")


@dataclass
class MemoryTranscripts(TranscriptRepository):
    calls: MemoryCalls
    lines: dict[CallId, list[TranscriptEntry]] = field(default_factory=lambda: defaultdict(list))

    async def append(
        self, user_id: UserId, call_id: CallId, entries: Sequence[TranscriptEntry]
    ) -> None:
        if await self.calls.get(user_id, call_id) is None:
            raise RecordNotFoundError("call", call_id.value)
        self.lines[call_id].extend(entries)

    async def for_call(self, user_id: UserId, call_id: CallId) -> tuple[TranscriptEntry, ...]:
        return tuple(self.lines[call_id])

    async def status(self, user_id: UserId, call_id: CallId) -> TranscriptStatus:
        return TranscriptStatus.RETAINED if self.lines[call_id] else TranscriptStatus.NOT_RECORDED


@dataclass
class MemorySummaries(SummaryRepository):
    calls: MemoryCalls
    stored: dict[CallId, CallSummary] = field(default_factory=dict)

    async def add(self, user_id: UserId, summary: CallSummary) -> None:
        if await self.calls.get(user_id, summary.call_id) is None:
            raise RecordNotFoundError("call", summary.call_id.value)
        if summary.call_id in self.stored:
            raise AlreadyRecordedError("summary", summary.call_id.value)
        self.stored[summary.call_id] = summary

    async def get(self, user_id: UserId, call_id: CallId) -> CallSummary | None:
        return self.stored.get(call_id)

    async def for_calls(
        self, user_id: UserId, call_ids: Sequence[CallId]
    ) -> Mapping[CallId, CallSummary]:
        return {each: self.stored[each] for each in call_ids if each in self.stored}


@dataclass
class MemoryCallStores:
    """Orchestration's storage, which can be told to stop answering."""

    users: InMemoryUserRepository = field(default_factory=InMemoryUserRepository)
    preferences: InMemoryPreferencesRepository = field(
        default_factory=InMemoryPreferencesRepository
    )
    calls: MemoryCalls = field(default_factory=MemoryCalls)
    unavailable: bool = False

    def __post_init__(self) -> None:
        self.transcripts = MemoryTranscripts(self.calls)
        self.summaries = MemorySummaries(self.calls)

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[CallStores]:
        if self.unavailable:
            raise ConnectionError("the database is not answering")
        yield CallStores(
            users=self.users,
            preferences=self.preferences,
            calls=self.calls,
            transcripts=self.transcripts,
            summaries=self.summaries,
        )

    async def with_owner(self, preferences: UserPreferences | None = None) -> None:
        await self.users.add(User(id=OWNER, phone_number=OWNERS_NUMBER))
        if preferences is not None:
            await self.preferences.save(OWNER, preferences)

    def call(self, call_id: str) -> CallSession:
        return self.calls.stored[CallId(call_id)]

    def states(self, call_id: str) -> list[CallState]:
        return self.calls.states[CallId(call_id)]


class EveryCallIsTheOwners(CallOwnership):
    """Every call is the one user's, except those whose identifier names nobody or another user."""

    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        call = incoming.call_id.value
        if call.startswith("nobody"):
            return None
        return ANOTHER_OWNER if call.startswith("another") else OWNER


# ------------------------------------------------------------------------------------ lines


class CallAudio(AudioSource):
    """A call's audio, arriving until the line says it has stopped."""

    def __init__(self) -> None:
        self._frames: asyncio.Queue[AudioFrame | None] = asyncio.Queue()

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    async def frames(self) -> AsyncIterator[AudioFrame]:
        while True:
            frame = await self._frames.get()
            if frame is None:
                return
            yield frame

    def stop(self) -> None:
        self._frames.put_nowait(None)


class CallSpeaker(AudioSink):
    """Where the assistant's voice goes: nowhere, counted."""

    def __init__(self) -> None:
        self.written = 0

    @property
    def format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    async def write(self, frame: AudioFrame) -> None:
        self.written += 1

    async def discard(self) -> None:
        return None


class Line(CallTransport):
    """A transport a test drives. What it may be asked depends on the subclass's capabilities.

    `refusing` names requests that raise as a provider refusing them; `failing` names requests that
    raise as a provider that cannot be reached, which trying again may get past; `holding` names
    requests that wait until their event is set, which is how a request that never returns is made.
    """

    def __init__(self) -> None:
        self._events: asyncio.Queue[CallEvent | None] = asyncio.Queue()
        self._numbers = 0
        self.requests: list[tuple[str, CallId]] = []
        # Every event delivered twice, as providers do.
        self.duplicating = False
        self.refusing: set[str] = set()
        self.failing: set[str] = set()
        self.holding: dict[str, asyncio.Event] = {}

    @property
    def name(self) -> str:
        return "line"

    async def events(self) -> AsyncIterator[CallEvent]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    def report(self, kind: CallEventKind, call: str, **details: object) -> CallEvent:
        """Say something happened on a call, under a fresh event identifier."""
        self._numbers += 1
        event = CallEvent(
            kind,
            CallId(call),
            EventId(f"{call}:{kind.value}:{self._numbers}"),
            **details,  # type: ignore[arg-type]
        )
        self._events.put_nowait(event)
        if self.duplicating:
            self._events.put_nowait(event)
        return event

    def repeat(self, event: CallEvent) -> None:
        """Deliver an event again, exactly as before: what every provider does."""
        self._events.put_nowait(event)

    def hangs_up(self, call: str) -> CallEvent:
        """The call is over, however the provider learned it."""
        return self.report(CallEventKind.ENDED, call)

    def report_failure(self, call: str) -> CallEvent:
        """Work on the call failed where only the provider could see it."""
        return self.report(CallEventKind.FAILED, call)

    def close(self) -> None:
        self._events.put_nowait(None)

    def asked(self, request: str, call: str) -> int:
        return self.requests.count((request, CallId(call)))

    async def terminate(self, call_id: CallId) -> None:
        await self._request("terminate", call_id)

    async def _request(self, request: str, call_id: CallId) -> None:
        self.requests.append((request, call_id))
        gate = self.holding.get(request)
        if gate is not None:
            await gate.wait()
        if request in self.refusing:
            raise ProviderError("line", f"{request} refused", retryable=False)
        if request in self.failing:
            raise ProviderError("line", f"{request} could not be reached", retryable=True)


class StreamingLine(Line):
    """Answers, carries audio both ways, and adds the user to a call."""

    def __init__(self) -> None:
        super().__init__()
        self.audio: dict[CallId, CallAudio] = defaultdict(CallAudio)
        self.speakers: dict[CallId, CallSpeaker] = defaultdict(CallSpeaker)
        self.dialled: list[PhoneNumber] = []

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_answer_under_program_control=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
            can_bridge_human=True,
            supports_three_way_call=True,
        )

    async def answer(self, call_id: CallId) -> None:
        await self._request("answer", call_id)

    def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        return self.audio[call_id].frames()

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        await self.speakers[call_id].write(frame)

    def audio_format(self) -> AudioFormat:
        return SPEECH_WIDEBAND

    def audio_source(self, call_id: CallId) -> AudioSource:
        return self.audio[call_id]

    def audio_sink(self, call_id: CallId) -> AudioSink:
        return self.speakers[call_id]

    async def add_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        self.dialled.append(number)
        await self._request("dial", call_id)

    async def remove_participant(self, call_id: CallId, number: PhoneNumber) -> None:
        await self._request("cancel", call_id)

    async def set_assistant_presence(self, call_id: CallId, presence: AssistantPresence) -> None:
        await self._request(f"presence:{presence.value}", call_id)

    # What the provider reports, in the vocabulary a streaming call uses.

    def arrives(self, call: str, caller: Caller | None = None) -> CallEvent:
        return self.report(CallEventKind.INCOMING, call, caller=caller)

    def assistant_joins(self, call: str) -> CallEvent:
        return self.report(
            CallEventKind.PARTICIPANT_JOINED, call, participant=ParticipantRole.ASSISTANT
        )

    def user_answers(self, call: str) -> CallEvent:
        return self.report(
            CallEventKind.PARTICIPANT_JOINED,
            call,
            participant=ParticipantRole.USER,
            outcome=ParticipantOutcome.ANSWERED,
        )

    def user_unreachable(self, call: str, outcome: ParticipantOutcome) -> CallEvent:
        return self.report(
            CallEventKind.PARTICIPANT_UNREACHABLE,
            call,
            participant=ParticipantRole.USER,
            outcome=outcome,
        )

    def report_unreachable_assistant(self, call: str) -> CallEvent:
        return self.report(
            CallEventKind.PARTICIPANT_UNREACHABLE,
            call,
            participant=ParticipantRole.ASSISTANT,
            outcome=ParticipantOutcome.FAILED,
        )

    def leaves(self, call: str, who: ParticipantRole) -> CallEvent:
        return self.report(CallEventKind.PARTICIPANT_LEFT, call, participant=who)


class HandsetLine(Line):
    """A handset's own calls: screened before they ring, and reported afterwards."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(can_screen_before_ringing=True, supports_native_ringing=True)

    def screening_decisions(self) -> frozenset[ScreeningDecision]:
        return frozenset(ScreeningDecision)

    def screening_deadline(self) -> timedelta:
        return timedelta(seconds=5)

    def arrives(
        self, call: str, decision: ScreeningDecision | None = ScreeningDecision.ALLOW
    ) -> CallEvent:
        return self.report(CallEventKind.INCOMING, call, screening=decision)

    def picked_up(self, call: str) -> CallEvent:
        return self.report(CallEventKind.ANSWERED, call)


# ------------------------------------------------------------------------------------ agent


@dataclass
class Look:
    """What the scripted agent does on one look at a call, in the order a real judgement does it.

    It records what it records, then concludes through the real conclusion: the escalation the
    policy makes of `proposal`, then `ending` if the rules still allow it. `waits_for` holds the
    look at its start; `fails` raises instead of concluding.
    """

    proposal: EscalationProposal = ROUTINE
    ending: CallEnding | None = None
    record: OutcomeRecord | None = None
    message: str | None = None
    fails: Exception | None = None
    waits_for: asyncio.Event | None = None


class ScriptedAgent(CallAgent):
    """Takes one scripted look per judgement; out of looks, it assesses the call as routine."""

    def __init__(self, looks: list[Look], actions: CallActions, service: EscalationService) -> None:
        self._looks = looks
        self._actions = actions
        self._conclusion = JudgementConclusion(actions, service)
        self.judged: list[CallSoFar] = []

    async def judge(self, call: CallSoFar) -> AgentJudgement:
        self.judged.append(call)
        look = self._looks.pop(0) if self._looks else Look()
        if look.waits_for is not None:
            await look.waits_for.wait()
        if look.fails is not None:
            raise look.fails
        if look.record is not None:
            await self._actions.record_outcome(call.call_id, look.record)
        if look.message is not None:
            await self._actions.take_message(call.call_id, look.message)
        notes = JudgementNotes()
        if look.ending is not None:
            notes.ending_requested(look.ending)
        return await self._conclusion.conclude(call, notes, look.proposal)


@dataclass
class Agent:
    """The scripted agent as bootstrap would wire it, and what it forgot."""

    looks: list[Look] = field(default_factory=list)
    forgotten: list[CallId] = field(default_factory=list)
    built: ScriptedAgent | None = None

    def judging(self, actions: CallActions) -> CallJudging:
        service = EscalationService(actions)

        def forget(call_id: CallId) -> None:
            self.forgotten.append(call_id)
            service.forget(call_id)

        self.built = ScriptedAgent(self.looks, actions, service)
        return CallJudging(agent=self.built, forget=forget)


class ControlledSession(EchoSpeechSession):
    """An echo session whose context updates can be refused."""

    def __init__(self) -> None:
        super().__init__()
        self.refusing_updates = False

    async def update_context(self, context: str) -> None:
        if self.refusing_updates:
            raise ProviderError("echo", "the update was refused", retryable=False)
        await super().update_context(context)


class ControlledSpeech(EchoSpeechProvider):
    """Opens sessions a test controls, from a service a test can make refuse or never answer."""

    def __init__(self) -> None:
        super().__init__()
        self.refusing = False
        self.never_answering = False
        self.refusing_updates = False

    async def connect(
        self, *, system_context: str, voice_id: str, locale: str, input_format: AudioFormat
    ) -> SpeechSession:
        if self.never_answering:
            await asyncio.Event().wait()
        if self.refusing:
            raise ProviderError("echo", "the speech service refused", retryable=True)
        self.connections.append({"system_context": system_context, "voice_id": voice_id})
        session = ControlledSession()
        session.refusing_updates = self.refusing_updates
        self.sessions.append(session)
        return session


class HeldNotifications(RecordingNotificationProvider):
    """Delivers once `released` is set, which a test leaves unset to hold a notification up."""

    def __init__(self) -> None:
        super().__init__()
        self.released = asyncio.Event()
        self.released.set()

    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        await self.released.wait()
        return await super().send(token, notification)


class WritingSummariser(CallSummariser):
    """Writes the facts' summary under a headline of its own, or never answers when `hanging`."""

    HEADLINE: Final = "A model's account of the call."

    def __init__(self) -> None:
        self.asked: list[tuple[CallFacts, tuple[TranscriptEntry, ...]]] = []
        self.hanging = False

    async def summarise(
        self, facts: CallFacts, transcript: Sequence[TranscriptEntry], *, locale: str
    ) -> CallSummary:
        self.asked.append((facts, tuple(transcript)))
        if self.hanging:
            await asyncio.Event().wait()
        return replace(fallback_summary(facts, locale=locale), headline=self.HEADLINE)


def an_assistance() -> Assistance:
    """What an assistant step speaks with, for a test that only looks at plans."""
    return Assistance(
        speech=EchoSpeechProvider(),
        voices=StaticVoiceProvider(),
        judging=Agent().judging(RecordingCallActions()),
    )


# ------------------------------------------------------------------------------ the running


@dataclass
class Running:
    """An orchestrator on a line, and everything around it a test reads back."""

    line: Line
    orchestrator: CallOrchestrator
    stores: MemoryCallStores
    speech: ControlledSpeech
    agent: Agent
    notifications: HeldNotifications
    escalations: InMemoryStores
    dispatcher: EscalationDispatcher
    metrics: RecordingMetrics
    tracer: RecordingTracer
    circuits: Circuits
    summariser: WritingSummariser

    async def settled(self, call: str, state: CallState) -> CallSession:
        """Wait until the stored call is in `state`, and return it."""
        identifier = CallId(call)
        stored = self.stores.calls.stored
        await eventually(lambda: identifier in stored and stored[identifier].state is state)
        return stored[identifier]

    async def ended(self, call: str) -> CallSession:
        """Wait until the call's run has gone and its summary is written, and return the call."""
        identifier = CallId(call)
        await eventually(
            lambda: (
                identifier not in self.orchestrator._runs
                and identifier in self.stores.summaries.stored
            )
        )
        return self.stores.call(call)

    @property
    def judgements(self) -> int:
        """How many looks at calls the agent has taken."""
        return 0 if self.agent.built is None else len(self.agent.built.judged)

    async def quiet(self) -> None:
        """Let background notifications finish."""
        await self.dispatcher.aclose()

    async def session(self, index: int = 0) -> EchoSpeechSession:
        """The speech session opened for the call, once there is one."""
        await eventually(lambda: len(self.speech.sessions) > index)
        return self.speech.sessions[index]

    async def caller_says(self, text: str, index: int = 0) -> None:
        session = await self.session(index)
        await session.emit(TranscriptProduced(text, speaker_is_caller=True, is_final=True))


async def eventually(condition: Callable[[], bool]) -> None:
    """Wait until `condition` holds, for at most a few seconds."""
    async with asyncio.timeout(5):
        # What is waited on is plain state in a fake or a store, which offers nothing to await.
        while not condition():  # noqa: ASYNC110
            await asyncio.sleep(0.005)


def running_tasks() -> set[asyncio.Task[object]]:
    return {task for task in asyncio.all_tasks() if task is not asyncio.current_task()}


@asynccontextmanager
async def orchestrating(
    line: Line,
    *,
    preferences: UserPreferences | None = None,
    looks: Sequence[Look] = (),
    bounds: Bounds = QUICK,
    stores: MemoryCallStores | None = None,
    start: bool = True,
    circuit_policy: CircuitPolicy | None = None,
) -> AsyncIterator[Running]:
    """An orchestrator on `line`, started, and stopped afterwards with nothing of it left running.

    Counts the tasks alive before and after: a run, a conversation, a judgement or a timer outliving
    the orchestrator fails the test that left it.
    """
    before = running_tasks()
    storage = stores or MemoryCallStores()
    if stores is None:
        await storage.with_owner(preferences)
    escalations = InMemoryStores()
    await escalations.devices.register(OWNER, DEVICE)
    notifications = HeldNotifications()
    metrics = RecordingMetrics()
    tracer = RecordingTracer()
    circuits = Circuits(metrics=metrics, policy=circuit_policy)
    dispatcher = EscalationDispatcher(
        providers=[notifications],
        stores=escalations.scope,
        metrics=metrics,
        tracer=tracer,
        circuits=circuits,
    )
    speech = ControlledSpeech()
    agent = Agent(list(looks))
    summariser = WritingSummariser()
    orchestrator = CallOrchestrator(
        transport=line,
        ownership=EveryCallIsTheOwners(),
        stores=storage.scope,
        dispatcher=dispatcher,
        clock=FixedClock(),
        metrics=metrics,
        tracer=tracer,
        circuits=circuits,
        assistant=AssistantServices(
            speech=speech, voices=StaticVoiceProvider(), judging=agent.judging
        ),
        summariser=summariser,
        bounds=bounds,
    )
    running = Running(
        line=line,
        orchestrator=orchestrator,
        stores=storage,
        speech=speech,
        agent=agent,
        notifications=notifications,
        escalations=escalations,
        dispatcher=dispatcher,
        metrics=metrics,
        tracer=tracer,
        circuits=circuits,
        summariser=summariser,
    )
    if start:
        await orchestrator.start()
    try:
        yield running
    finally:
        await orchestrator.stop()
        line.close()
        await dispatcher.aclose()
        assert orchestrator.live_calls == 0
        await eventually(lambda: not running_tasks() - before)
