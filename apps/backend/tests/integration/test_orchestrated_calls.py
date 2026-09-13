"""Whole calls, orchestrated: the simulated provider, PostgreSQL, echo speech and a scripted model.

The orchestrator here is the one bootstrap builds, over the streaming transport on loopback, storing
through real units of work with every caller, line and summary sealed. Only the speech service
echoes and the model reads from a script; the SDK's agent loop, the tools, the escalation policy and
the conclusion all run for real. Each flow ends by counting what is left: calls held by the
transport, media sockets, runs.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.repositories import (
    SqlDeviceRepository,
    SqlEscalationContextRepository,
    SqlPreferencesRepository,
    SqlUserRepository,
)
from letmehandle.adapters.database.session import create_session_factory, unit_of_work
from letmehandle.adapters.transport.twilio import transport as transport_module
from letmehandle.application.orchestration.ports import AssistantServices
from letmehandle.bootstrap import (
    build_call_orchestrator,
    build_container,
    build_escalation_dispatcher,
    call_judging_on,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.call import CallHandling, CallSession, Participant, ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.caller import Caller
from letmehandle.domain.models.escalation_context import NotificationDelivery
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.preferences import CallRules, HandlingPosture, UserPreferences
from letmehandle.domain.models.summary import CallOutcome
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.notification import DevicePlatform, DeviceToken
from letmehandle.domain.ports.repositories import MAX_CALL_PAGE
from letmehandle.domain.ports.speech import SessionFailed, TranscriptProduced
from tests.contracts.fakes import EchoSpeechProvider, RecordingNotificationProvider
from tests.support.config import TEST_TRANSCRIPT_KEYS, make_settings
from tests.support.recording_metrics import RecordingMetrics
from tests.support.scripted_model import ScriptedModel, assess
from tests.support.simulated_twilio import Answering, Deployment, eventually, simulated_deployment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from letmehandle.application.orchestration.orchestrator import CallOrchestrator
    from letmehandle.bootstrap import Container
    from letmehandle.domain.ports.notification import DeliveryOutcome, EscalationNotification
    from tests.support.scripted_model import Step

pytestmark = pytest.mark.integration

CALL = "CAsim-orchestrated"
USER = UserId("user-orchestrated")
USERS_LINE = PhoneNumber.parse("+12025550143")
PHONE = DeviceToken(DevicePlatform.IOS, "a-device-token")
ROUTINE = assess(importance="routine")
WANTS_THE_USER = assess(importance="urgent", caller_asked_for_the_user=True)


class RefusingNotifications(RecordingNotificationProvider):
    """A push service that fails every delivery the way a broken one does: by raising."""

    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        raise ProviderError("push", "the push service is down", retryable=True)


class Orchestrated:
    """One deployment, orchestrated, and what a flow reads back from storage."""

    def __init__(
        self,
        deployment: Deployment,
        orchestrator: CallOrchestrator,
        factory: async_sessionmaker[AsyncSession],
        container: Container,
        speech: EchoSpeechProvider,
    ) -> None:
        self.deployment = deployment
        self.provider = deployment.provider
        self.orchestrator = orchestrator
        self.factory = factory
        self.container = container
        self.speech = speech

    async def stored(self, call: str = CALL) -> CallSession | None:
        cipher = self.container.transcript_cipher
        assert cipher is not None
        async with unit_of_work(self.factory) as session:
            return await SqlCallRepository(session, cipher, self.container.clock).get(
                USER, CallId(call)
            )

    async def reaches(self, state: CallState, call: str = CALL) -> CallSession:
        async with asyncio.timeout(10):
            while True:
                stored = await self.stored(call)
                if stored is not None and stored.state is state:
                    return stored
                await asyncio.sleep(0.02)

    async def summary_outcome(self, call: str = CALL) -> CallOutcome | None:
        cipher = self.container.transcript_cipher
        assert cipher is not None
        async with unit_of_work(self.factory) as session:
            summary = await SqlSummaryRepository(session, cipher, self.container.clock).get(
                USER, CallId(call)
            )
        return None if summary is None else summary.outcome

    async def ended(self, call: str = CALL) -> CallSession:
        async with asyncio.timeout(10):
            # Storage offers nothing to await, so it is asked again until it answers.
            while await self.summary_outcome(call) is None:  # noqa: ASYNC110
                await asyncio.sleep(0.02)
        await eventually(lambda: self.orchestrator.live_calls == 0)
        await self.deployment.settle()
        stored = await self.stored(call)
        assert stored is not None
        return stored

    async def arrives(self) -> None:
        await self.provider.place_call(CALL, forwarded_from=USERS_LINE)

    async def caller_says(self, text: str) -> None:
        await eventually(lambda: bool(self.speech.sessions))
        await self.speech.sessions[0].emit(
            TranscriptProduced(text, speaker_is_caller=True, is_final=True)
        )

    def user_is_on_the_call(self) -> bool:
        return any(
            leg.to == USERS_LINE.value and leg.in_conference for leg in self.provider.legs.values()
        )

    def nothing_held(self) -> None:
        transport = self.deployment.transport
        assert transport.active_calls == 0
        assert transport.open_media_sockets == 0
        assert transport.pending_tasks == 0
        assert self.orchestrator.live_calls == 0
        assert all(session.is_closed for session in self.speech.sessions)


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "LATE_CALLBACK_GRACE_SECONDS", 0.05)


async def seed(
    factory: async_sessionmaker[AsyncSession],
    container: Container,
    preferences: UserPreferences,
    *,
    device: bool,
) -> None:
    async with unit_of_work(factory) as session:
        clock = container.clock
        await SqlUserRepository(session, clock).add(User(id=USER, phone_number=USERS_LINE))
        await SqlPreferencesRepository(session, clock).save(USER, preferences)
        if device:
            await SqlDeviceRepository(session, clock).register(USER, PHONE)


@pytest.fixture
def storage(session: object, database_url: str, schema: str) -> tuple[str, str]:
    """The schema the `session` fixture created, for an application engine of its own."""
    return database_url, schema


@asynccontextmanager
async def orchestrating(
    storage: tuple[str, str],
    *,
    steps: list[Step] | None = None,
    preferences: UserPreferences | None = None,
    notifications: RecordingNotificationProvider | None = None,
    before_start: Callable[[async_sessionmaker[AsyncSession], Container], Awaitable[None]]
    | None = None,
) -> AsyncIterator[Orchestrated]:
    database_url, schema = storage
    engine = create_async_engine(
        database_url, connect_args={"server_settings": {"search_path": schema}}
    )
    factory = create_session_factory(engine)
    try:
        async with simulated_deployment(collect_events=False) as deployment:
            settings = make_settings(transcript_encryption_keys=TEST_TRANSCRIPT_KEYS)
            container = build_container(
                settings,
                voices=deployment.app.state.voices,
                reported_calls=deployment.app.state.reported_calls,
            )
            if notifications is not None:
                container = replace(container, notifications=(notifications,))
            await seed(
                factory,
                container,
                preferences or UserPreferences(),
                device=notifications is not None,
            )
            if before_start is not None:
                await before_start(factory, container)
            dispatcher = build_escalation_dispatcher(container, factory, metrics=RecordingMetrics())
            speech = EchoSpeechProvider()
            model = ScriptedModel(steps or [ROUTINE] * 4)
            orchestrator = build_call_orchestrator(
                settings,
                container=container,
                session_factory=factory,
                telephony=deployment.binding,
                dispatcher=dispatcher,
                metrics=RecordingMetrics(),
                assistant=AssistantServices(
                    speech=speech,
                    voices=container.voices,
                    judging=lambda actions: call_judging_on(
                        model, actions=actions, timeout=timedelta(seconds=5)
                    ),
                ),
            )
            await orchestrator.start()
            try:
                yield Orchestrated(deployment, orchestrator, factory, container, speech)
            finally:
                await orchestrator.stop()
                await dispatcher.aclose()
    finally:
        await engine.dispose()


async def test_a_call_put_through_rings_the_user_and_is_recorded(storage: tuple[str, str]) -> None:
    passing = UserPreferences(rules=CallRules(default_posture=HandlingPosture.PASS_THROUGH))
    async with orchestrating(storage, preferences=passing) as running:
        running.provider.answering[USERS_LINE.value] = Answering.ANSWERS
        await running.arrives()
        await eventually(running.user_is_on_the_call)
        await running.provider.user_hangs_up(USERS_LINE)
        call = await running.ended()

        assert call.state is CallState.COMPLETED
        assert call.handling is CallHandling.PASSED_THROUGH
        assert await running.summary_outcome() is CallOutcome.PASSED_THROUGH
        assert running.speech.sessions == []
        running.nothing_held()


async def test_the_assistant_takes_a_call_on_its_own(storage: tuple[str, str]) -> None:
    async with orchestrating(storage) as running:
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await eventually(lambda: running.deployment.transport.open_media_sockets == 1)
        await running.provider.send_caller_audio(CALL, b"\x11" * 160, frames=2)
        await running.caller_says("I am calling about the boiler service.")
        await asyncio.sleep(0.1)
        await running.provider.caller_hangs_up(CALL)
        call = await running.ended()

        assert call.state is CallState.COMPLETED
        assert call.handling is CallHandling.ASSISTANT
        assert call.has_participant(ParticipantRole.AGENT) or call.participants
        cipher = running.container.transcript_cipher
        assert cipher is not None
        async with unit_of_work(running.factory) as session:
            lines = await SqlTranscriptRepository(session, cipher).for_call(USER, CallId(CALL))
        assert [line.text for line in lines] == ["I am calling about the boiler service."]
        assert await running.summary_outcome() is CallOutcome.CALLER_HUNG_UP
        running.nothing_held()


async def test_an_escalation_answered_hands_the_call_to_the_user(storage: tuple[str, str]) -> None:
    async with orchestrating(storage, steps=[WANTS_THE_USER]) as running:
        running.provider.answering[USERS_LINE.value] = Answering.ANSWERS
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await running.caller_says("Can I speak to her, please? It is urgent.")
        await running.reaches(CallState.HUMAN_JOINED)
        await running.provider.user_hangs_up(USERS_LINE)
        call = await running.ended()

        assert call.state is CallState.COMPLETED
        assert call.escalated_at is not None
        assert await running.summary_outcome() is CallOutcome.HANDED_TO_USER
        running.nothing_held()


async def test_an_escalation_nobody_answers_returns_the_call_to_the_assistant(
    storage: tuple[str, str],
) -> None:
    async with orchestrating(storage, steps=[WANTS_THE_USER]) as running:
        running.provider.answering[USERS_LINE.value] = Answering.RINGS_OUT
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await running.caller_says("Is she there?")
        session = running.speech.sessions[0]
        await eventually(lambda: any("no_answer" in each for each in session.context_updates))
        handed_back = await running.reaches(CallState.AGENT_HANDLING)
        assert handed_back.escalated_at is not None
        await running.provider.caller_hangs_up(CALL)
        call = await running.ended()

        assert call.state is CallState.COMPLETED
        assert await running.summary_outcome() is CallOutcome.UNANSWERED_ESCALATION
        running.nothing_held()


async def test_the_caller_hanging_up_while_the_user_rings_cancels_the_ring(
    storage: tuple[str, str],
) -> None:
    async with orchestrating(storage, steps=[WANTS_THE_USER]) as running:
        running.provider.answering[USERS_LINE.value] = Answering.KEEPS_RINGING
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await running.caller_says("Please put her on.")
        await running.reaches(CallState.HUMAN_RINGING)
        await running.provider.caller_hangs_up(CALL)
        call = await running.ended()

        assert call.state is CallState.COMPLETED
        assert running.provider.user_leg(USERS_LINE).finished
        running.nothing_held()


async def test_speech_failing_mid_call_fails_the_call_and_ends_it_for_the_caller(
    storage: tuple[str, str],
) -> None:
    async with orchestrating(storage) as running:
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await eventually(lambda: bool(running.speech.sessions))
        await running.speech.sessions[0].emit(
            SessionFailed("the service went away", retryable=False)
        )
        call = await running.ended()

        assert call.state is CallState.FAILED
        assert running.provider.conference_of(CALL).ended
        assert await running.summary_outcome() is CallOutcome.FAILED
        running.nothing_held()


async def test_a_notification_that_fails_leaves_the_escalation_ringing(
    storage: tuple[str, str],
) -> None:
    notifications = RefusingNotifications()
    async with orchestrating(
        storage, steps=[WANTS_THE_USER], notifications=notifications
    ) as running:
        running.provider.answering[USERS_LINE.value] = Answering.ANSWERS
        await running.arrives()
        await running.reaches(CallState.AGENT_HANDLING)
        await running.caller_says("I need to speak to her now.")
        await running.reaches(CallState.HUMAN_JOINED)
        async with asyncio.timeout(10):
            while True:
                async with unit_of_work(running.factory) as session:
                    context = await SqlEscalationContextRepository(session).get(USER, CallId(CALL))
                if context is not None and context.delivery is NotificationDelivery.FAILED:
                    break
                await asyncio.sleep(0.02)
        await running.provider.caller_hangs_up(CALL)
        await running.ended()
        running.nothing_held()


async def test_a_restart_ends_the_call_the_last_process_left_running(
    storage: tuple[str, str],
) -> None:
    async def left_by_a_stopped_process(
        factory: async_sessionmaker[AsyncSession], container: Container
    ) -> None:
        cipher = container.transcript_cipher
        assert cipher is not None
        started = container.clock.now() - timedelta(minutes=2)
        call = CallSession.restore(
            id=CallId("CAsim-left-running"),
            user_id=USER,
            caller=Caller(number=PhoneNumber.parse("+12025550123")),
            started_at=started,
            state=CallState.HUMAN_RINGING,
            participants=(Participant(ParticipantRole.AGENT, started),),
            ended_at=None,
            handling=CallHandling.ASSISTANT,
            escalated_at=started,
        )
        async with unit_of_work(factory) as session:
            await SqlCallRepository(session, cipher, container.clock).save(call)

    async with orchestrating(storage, before_start=left_by_a_stopped_process) as running:
        call = await running.stored("CAsim-left-running")
        assert call is not None
        assert call.state is CallState.FAILED
        assert call.ended_at is not None
        assert await running.summary_outcome("CAsim-left-running") is CallOutcome.FAILED
        cipher = running.container.transcript_cipher
        assert cipher is not None
        async with unit_of_work(running.factory) as session:
            repository = SqlCallRepository(session, cipher, running.container.clock)
            assert await repository.unfinished(limit=MAX_CALL_PAGE) == ()
