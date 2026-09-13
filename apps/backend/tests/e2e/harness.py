"""The whole system, running, with a controllable stand-in wherever the world would be.

A system here is the application `create_app` builds, served over real HTTP on loopback with its
real lifespan — so the orchestrator is the one that lifespan starts, storing in PostgreSQL through
the application's own engine — and the stand-ins it talks to:

- telephony: the simulated provider, calling the application back with signed requests and opening
  a real media websocket to it; or a handset, reporting over the application's own route;
- speech: an echo session a scenario speaks through, or the real speech adapter against the
  simulated realtime service when what is under test is the speech connection itself;
- the model: a script, run inside the real agent loop, tools and escalation policy;
- push: a recording provider per platform, behind the real dispatcher.

Every scenario ends by counting what is left, and by reading what the run emitted for anything
that identifies a caller.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import letmehandle
from letmehandle.adapters.database.call_repositories import SqlCallRepository
from letmehandle.adapters.database.session import unit_of_work
from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport
from letmehandle.application.orchestration.orchestrator import CallOrchestrator
from letmehandle.application.orchestration.ports import AssistantServices
from letmehandle.bootstrap import (
    build_call_transport,
    build_reported_calls,
    build_voice_provider,
    call_judging_on,
)
from letmehandle.config.settings import TelephonyProviderName
from letmehandle.domain.models.call import ParticipantRole
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import CallTransport
from letmehandle.domain.ports.speech import TranscriptProduced
from letmehandle.main import create_app
from tests.contracts.fakes import EchoSpeechProvider, EchoSpeechSession
from tests.e2e.app_client import AppClient, call_handling
from tests.support.config import TEST_TRANSCRIPT_KEYS, make_settings
from tests.support.scripted_model import ScriptedModel
from tests.support.simulated_realtime_service import SIMULATED_API_KEY, SIMULATED_MODEL
from tests.support.simulated_twilio import (
    OUR_NUMBER,
    PUBLIC_BASE_URL,
    SIMULATED_ACCOUNT,
    SIMULATED_APP,
    SIMULATED_TOKEN,
    SimulatedLeg,
    SimulatedTwilio,
    eventually,
    serving,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from fastapi import FastAPI

    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.orchestration.ports import CallJudging
    from letmehandle.config.settings import Settings
    from letmehandle.domain.models.audio import AudioFormat, AudioFrame
    from letmehandle.domain.models.call import CallSession
    from letmehandle.domain.ports.audio_io import AudioSink, AudioSource
    from letmehandle.domain.ports.call_transport import CallEvent, TransportCapabilities
    from letmehandle.domain.ports.notification import EscalationNotification
    from letmehandle.domain.ports.speech import SpeechProvider
    from tests.contracts.fakes import RecordingNotificationProvider
    from tests.e2e.app_client import Account, Json
    from tests.support.scripted_model import Step

# Fictional numbers, every one in the range reserved for fiction.
USERS_LINE: Final = "+12025550143"
CALLER: Final = "+12025550123"
DRIVER: Final = "+12025550124"
IMPORTANT_CALLER: Final = "+12025550145"

# How long one judgement may take the scripted model. It answers at once, so this only bounds a
# scenario that has gone wrong.
JUDGEMENT: Final = timedelta(seconds=5)

# How long a scenario waits for something the system is about to do. Everything here happens over
# loopback in milliseconds; a wait that runs this long is a defect, not a slow machine.
PATIENCE_SECONDS: Final = 10.0

_PRODUCT: Final = Path(letmehandle.__file__).parent


@dataclass(frozen=True, slots=True)
class Pushes:
    """The push provider for each platform, and everything they were asked to deliver."""

    ios: RecordingNotificationProvider
    android: RecordingNotificationProvider

    def sent(self) -> list[EscalationNotification]:
        return [notification for _, notification in (*self.ios.sent, *self.android.sent)]


def product_tasks() -> set[asyncio.Task[object]]:
    """The running tasks whose code is the product's, as opposed to the server's or a stand-in's."""
    tasks: set[asyncio.Task[object]] = set()
    for task in asyncio.all_tasks():
        code = getattr(task.get_coro(), "cr_code", None)
        if code is not None and Path(code.co_filename).is_relative_to(_PRODUCT):
            tasks.add(task)
    return tasks


class WithoutBridging(CallTransport):
    """The streaming transport with bridging withheld.

    A capability set that answers and carries a conversation but cannot add anybody to a call —
    the one on which escalation has no step to take. Everything it does declare is the real
    transport's, so the only difference a scenario on it can see is the missing capability.
    """

    def __init__(self, inner: TwilioCallTransport) -> None:
        self.inner = inner

    @property
    def name(self) -> str:
        return "streaming-without-bridging"

    @property
    def capabilities(self) -> TransportCapabilities:
        return replace(
            self.inner.capabilities, can_bridge_human=False, supports_three_way_call=False
        )

    def events(self) -> AsyncIterator[CallEvent]:
        return self.inner.events()

    async def terminate(self, call_id: CallId) -> None:
        await self.inner.terminate(call_id)

    async def answer(self, call_id: CallId) -> None:
        await self.inner.answer(call_id)

    def audio_format(self) -> AudioFormat:
        return self.inner.audio_format()

    def stream_audio(self, call_id: CallId) -> AsyncIterator[AudioFrame]:
        return self.inner.stream_audio(call_id)

    async def inject_audio(self, call_id: CallId, frame: AudioFrame) -> None:
        await self.inner.inject_audio(call_id, frame)

    def audio_source(self, call_id: CallId) -> AudioSource:
        return self.inner.audio_source(call_id)

    def audio_sink(self, call_id: CallId) -> AudioSink:
        return self.inner.audio_sink(call_id)


class System:
    """What every running system offers a scenario: the app's view, storage, and the counts."""

    def __init__(self, app: FastAPI, api: AppClient) -> None:
        self.app = app
        self.api = api
        self._baseline = product_tasks()

    @property
    def orchestrator(self) -> CallOrchestrator:
        orchestrator = self.app.state.orchestrator
        assert isinstance(orchestrator, CallOrchestrator)
        return orchestrator

    async def stored(self, account: Account, call_id: str) -> CallSession | None:
        """The call as storage holds it, read through the application's own repository."""
        container = self.app.state.container
        async with unit_of_work(self.app.state.session_factory) as session:
            repository = SqlCallRepository(session, container.transcript_cipher, container.clock)
            return await repository.get(account.user_id, CallId(call_id))

    async def stored_when(
        self, account: Account, call_id: str, condition: Callable[[CallSession], bool]
    ) -> CallSession:
        """The stored call, once it satisfies `condition`. Storage offers nothing to await."""
        async with asyncio.timeout(PATIENCE_SECONDS):
            while True:
                call = await self.stored(account, call_id)
                if call is not None and condition(call):
                    return call
                await asyncio.sleep(0.02)

    async def reaches(self, account: Account, call_id: str, state: CallState) -> CallSession:
        return await self.stored_when(account, call_id, lambda call: call.state is state)

    async def joined_by_the_user(self, account: Account, call_id: str) -> CallSession:
        """The call once the user has joined it and that is stored.

        The move and the join are two writes, so a read between them sees one without the other.
        """
        return await self.stored_when(
            account,
            call_id,
            lambda call: (
                call.state is CallState.HUMAN_JOINED and call.has_participant(ParticipantRole.HUMAN)
            ),
        )

    async def escalation_when(
        self, account: Account, call_id: str, condition: Callable[[Json], bool]
    ) -> Json:
        """The escalation as the app reads it, once it satisfies `condition`."""
        async with asyncio.timeout(PATIENCE_SECONDS):
            while True:
                escalation = await self.api.escalation(account, call_id)
                if escalation is not None and condition(escalation):
                    return escalation
                await asyncio.sleep(0.02)

    async def ended(self, account: Account, call_id: str) -> Json:
        """The call's detail once it has ended and its summary is written, and its run is gone."""
        async with asyncio.timeout(PATIENCE_SECONDS):
            while True:
                detail = await self.api.call(account, call_id)
                if detail is not None and detail["outcome"] is not None:
                    break
                await asyncio.sleep(0.02)
        await eventually(lambda: self.orchestrator.live_calls == 0, seconds=PATIENCE_SECONDS)
        return detail

    async def released(self) -> None:
        """Nothing the product started for a call is still running."""
        assert self.orchestrator.live_calls == 0
        await eventually(lambda: not product_tasks() - self._baseline, seconds=PATIENCE_SECONDS)


class StreamingSystem(System):
    """The application carrying calls on the streaming transport, and its simulated provider."""

    def __init__(
        self,
        app: FastAPI,
        api: AppClient,
        *,
        provider: SimulatedTwilio,
        model: ScriptedModel,
        speech: SpeechProvider,
    ) -> None:
        super().__init__(app, api)
        self.provider = provider
        self.model = model
        self.speech = speech

    @property
    def transport(self) -> TwilioCallTransport:
        transport = self.app.state.telephony.transport
        if isinstance(transport, WithoutBridging):
            transport = transport.inner
        assert isinstance(transport, TwilioCallTransport)
        return transport

    async def arrives(self, account: Account, call_id: str, *, caller: str = CALLER) -> None:
        """Somebody rings the user's own number, and their carrier forwards it to the product's."""
        await self.provider.place_call(call_id, caller, forwarded_from=account.number)

    @property
    def sessions(self) -> list[EchoSpeechSession]:
        """Every echo session opened, in order: one for each call the assistant took."""
        assert isinstance(self.speech, EchoSpeechProvider)
        return self.speech.sessions

    def session(self, index: int = -1) -> EchoSpeechSession:
        return self.sessions[index]

    async def caller_says(self, text: str) -> None:
        """A settled line from the caller, as the speech service transcribes it."""
        assert isinstance(self.speech, EchoSpeechProvider)
        speech = self.speech
        await eventually(lambda: bool(speech.sessions), seconds=PATIENCE_SECONDS)
        await self.session().emit(TranscriptProduced(text, speaker_is_caller=True, is_final=True))

    async def assistant_is_streaming(self, call_id: str) -> SimulatedLeg:
        """The assistant's leg, once its media stream is up at both ends."""

        def streaming() -> bool:
            return (
                any(
                    leg.to.startswith("app:") and leg.stream_sid is not None and leg.in_conference
                    for leg in self.provider.conference_of(call_id).legs
                )
                and self.transport.open_media_sockets > 0
            )

        await eventually(streaming, seconds=PATIENCE_SECONDS)
        return await self.provider.assistant_of(call_id)

    def user_is_on_the_call(self, number: str = USERS_LINE) -> bool:
        return any(leg.to == number and leg.in_conference for leg in self.provider.legs.values())

    def on_the_call(self, call_id: str) -> list[str]:
        """Who is in the call's conference right now, in the order they joined it."""
        return [
            _party(leg.to) for leg in self.provider.conference_of(call_id).legs if leg.in_conference
        ]

    def dialled(self, number: str = USERS_LINE) -> list[SimulatedLeg]:
        """Every leg the product dialled to `number`."""
        return [leg for leg in self.provider.legs.values() if leg.to == number]

    async def released(self) -> None:
        """Nothing is left anywhere: no run, task, call, socket, session or leg."""
        await self.provider.settle(self.transport)
        await super().released()
        transport = self.transport
        assert transport.active_calls == 0
        assert transport.open_media_sockets == 0
        assert transport.pending_tasks == 0
        assert not [leg.call_sid for leg in self.provider.legs.values() if leg.in_conference]
        assert not [leg.call_sid for leg in self.provider.legs.values() if not leg.finished]
        if isinstance(self.speech, EchoSpeechProvider):
            assert all(session.is_closed for session in self.speech.sessions)


def _party(to: str) -> str:
    if to.startswith("app:"):
        return "assistant"
    return "caller" if to == OUR_NUMBER.value else "user"


async def a_user(
    system: System, *, preferences: Json | None = None, devices: bool = True
) -> Account:
    """The user, signed in through the app, with their call handling set and, by default, a phone
    on each push platform."""
    account = await system.api.sign_in(USERS_LINE)
    await system.api.configure(account, preferences or call_handling())
    if devices:
        await system.api.register_device(account, "ios", "ios-device-token-e2e")
        await system.api.register_device(account, "android", "android-device-token-e2e")
    return account


class HandsetSystem(System):
    """The application whose calls are a handset's, reported after the handset decided them."""


def streaming_settings(database: str, *, speech_endpoint: str | None = None) -> Settings:
    """A deployment on the simulated telephony account, storing in `database`.

    With `speech_endpoint`, it speaks to that realtime service and asks for the caller's words.
    """
    return make_settings(
        database_url=database,
        log_level="info",
        transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
        telephony_provider=TelephonyProviderName.TWILIO,
        telephony_account_id=SIMULATED_ACCOUNT,
        telephony_auth_token=SIMULATED_TOKEN,
        telephony_numbers=(OUR_NUMBER,),
        telephony_app_id=SIMULATED_APP,
        telephony_webhook_base_url=PUBLIC_BASE_URL,
        speech_endpoint_url=speech_endpoint,
        speech_model=None if speech_endpoint is None else SIMULATED_MODEL,
        speech_api_key=None if speech_endpoint is None else SIMULATED_API_KEY,
        speech_transcription_model=None if speech_endpoint is None else "simulated-transcriber",
    )


@asynccontextmanager
async def streaming_system(
    database: str,
    *,
    steps: Sequence[Step] = (),
    speech: SpeechProvider | None = None,
    settings: Settings | None = None,
    provider: SimulatedTwilio | None = None,
    without_bridging: bool = False,
) -> AsyncIterator[StreamingSystem]:
    """The application on the streaming transport, with the model reading `steps`.

    `provider` is a simulated provider already carrying calls, for a system started in place of one
    that stopped; it is left open for whoever made it. Without one, a provider is made and closed.
    """
    chosen = settings or streaming_settings(database)
    simulated = provider or SimulatedTwilio()
    binding = build_call_transport(
        chosen, reported_calls=build_reported_calls(), http_transport=simulated.rest
    )
    assert binding is not None
    inner = binding.transport
    assert isinstance(inner, TwilioCallTransport)
    if without_bridging:
        binding = replace(binding, transport=WithoutBridging(inner))
    voices = build_voice_provider(chosen)
    model = ScriptedModel(steps)
    talking = speech or EchoSpeechProvider()

    def judging(actions: CallActions) -> CallJudging:
        return call_judging_on(model, actions=actions, timeout=JUDGEMENT)

    app = create_app(
        chosen,
        voices=voices,
        telephony=binding,
        assistant=AssistantServices(speech=talking, voices=voices, judging=judging),
    )
    async with serving(app) as url:
        if provider is None:
            simulated.attach(url)
        else:
            await simulated.move_to(url)
        api = AppClient(app, url)
        try:
            yield StreamingSystem(app, api, provider=simulated, model=model, speech=talking)
        finally:
            await api.aclose()
            if provider is None:
                await simulated.close()


@asynccontextmanager
async def handset_system(database: str) -> AsyncIterator[HandsetSystem]:
    """The application configured for handsets: nothing to converse with, nothing to dial."""
    settings = make_settings(
        database_url=database,
        log_level="info",
        transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
        telephony_provider=TelephonyProviderName.ANDROID_NATIVE,
    )
    app = create_app(settings)
    async with serving(app) as url:
        api = AppClient(app, url)
        try:
            yield HandsetSystem(app, api)
        finally:
            await api.aclose()


@dataclass(frozen=True, slots=True)
class Emitted:
    """Everything a run said to the outside world: each log line it wrote, each notification."""

    lines: list[str]
    pushes: Pushes

    def mentions(self, *texts: str) -> list[str]:
        """Which of `texts` appear anywhere in what was emitted."""
        # A capture that saw nothing would find nothing, and pass for the wrong reason.
        assert self.lines, "no log line was captured"
        everything = "\n".join([*self.lines, *map(repr, self.pushes.sent())])
        return [text for text in texts if text in everything]


def identifying(number: str) -> tuple[str, str]:
    """The ways a number can appear in text: as dialled, and as its national digits."""
    parsed = PhoneNumber.parse(number)
    return parsed.value, parsed.value.removeprefix("+1")
