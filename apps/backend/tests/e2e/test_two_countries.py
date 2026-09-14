"""One deployment serving the US and India, each country's calls on a line of its own (D-041)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

import pytest

from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport
from letmehandle.application.orchestration.ports import AssistantServices
from letmehandle.bootstrap import (
    build_call_transports,
    build_observability,
    build_reported_calls,
    build_voice_provider,
    call_judging_on,
)
from letmehandle.domain.models.call_state import CallState
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.speech import TranscriptProduced
from letmehandle.main import create_app
from tests.contracts.fakes import EchoSpeechProvider
from tests.e2e.app_client import AppClient, call_handling
from tests.e2e.harness import (
    CALLER,
    JUDGEMENT,
    PATIENCE_SECONDS,
    USERS_LINE,
    System,
    a_user,
)
from tests.support.config import TEST_TRANSCRIPT_KEYS, make_settings
from tests.support.recording_tracer import RecordingTracer
from tests.support.scripted_model import ScriptedModel, assess
from tests.support.simulated_twilio import (
    PUBLIC_BASE_URL,
    Answering,
    SimulatedTwilio,
    eventually,
    one_api_for,
    serving,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.orchestration.ports import CallJudging
    from letmehandle.bootstrap import CallTransportBinding
    from tests.e2e.harness import Pushes

pytestmark = pytest.mark.integration

US_LINE: Final = PhoneNumber.parse("+12025550100")
# India's numbers here are shorter than any in India's plan, so they reach nobody.
IN_LINE: Final = PhoneNumber.parse("+91555010")
IN_USERS_LINE: Final = "+91555001"
IN_CALLER: Final = "+91555002"
# A US number on the India line's account, which can ring Indian phones where its own cannot.
IN_DIALS_FROM: Final = PhoneNumber.parse("+12025550102")

LINES: Final = (
    f"us:provider=twilio;regions=US;numbers={US_LINE.value};account=account-us;app=app-us;"
    f"webhook={PUBLIC_BASE_URL},"
    f"in:provider=twilio;regions=IN;numbers={IN_LINE.value};account=account-in;app=app-in;"
    f"webhook={PUBLIC_BASE_URL}"
)
WANTS_THE_USER: Final = assess(importance="urgent", caller_asked_for_the_user=True)


def said(text: str) -> TranscriptProduced:
    """A settled line from the caller, as the speech service transcribes it."""
    return TranscriptProduced(text, speaker_is_caller=True, is_final=True)


@dataclass(frozen=True, slots=True)
class TwoLines:
    """A running deployment with a US and an India line, each at a simulated account."""

    us: SimulatedTwilio
    india: SimulatedTwilio
    bindings: tuple[CallTransportBinding, ...]
    system: System
    api: AppClient
    speech: EchoSpeechProvider

    async def released(self) -> None:
        """Both accounts settled, and nothing any call started is left."""
        for provider, binding in zip((self.us, self.india), self.bindings, strict=True):
            transport = binding.transport
            assert isinstance(transport, TwilioCallTransport)
            await provider.settle(transport)
            assert transport.active_calls == 0
            assert transport.open_media_sockets == 0
        await self.system.released()


@asynccontextmanager
async def two_lines(database: str, lines: str, model: ScriptedModel) -> AsyncIterator[TwoLines]:
    """The app serving `lines`, with the US and India accounts calling it back."""
    us = SimulatedTwilio(
        account="account-us", token="token-us", number=US_LINE, path_prefix="/lines/us"
    )
    india = SimulatedTwilio(
        account="account-in", token="token-in", number=IN_LINE, path_prefix="/lines/in"
    )
    settings = make_settings(
        database_url=database,
        log_level="info",
        transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
        telephony_lines=lines,
        telephony_line_auth_tokens="us:token-us,in:token-in",
    )
    observability = replace(build_observability(settings), tracer=RecordingTracer())
    bindings = build_call_transports(
        settings,
        reported_calls=build_reported_calls(),
        observability=observability,
        http_transport=one_api_for(us, india),
    )
    voices = build_voice_provider(settings)
    speech = EchoSpeechProvider()

    def judging(actions: CallActions) -> CallJudging:
        return call_judging_on(model, actions=actions, timeout=JUDGEMENT)

    app = create_app(
        settings,
        voices=voices,
        telephony=bindings,
        assistant=AssistantServices(speech=speech, voices=voices, judging=judging),
        observability=observability,
    )
    async with serving(app) as url:
        us.attach(url)
        india.attach(url)
        api = AppClient(app, url)
        try:
            yield TwoLines(us, india, tuple(bindings), System(app, api), api, speech)
        finally:
            await api.aclose()
            await us.close()
            await india.close()


async def test_a_us_call_and_an_indian_call_each_reach_their_user_from_their_own_line(
    database: str, pushes: Pushes
) -> None:
    model = ScriptedModel([WANTS_THE_USER, WANTS_THE_USER])
    async with two_lines(database, LINES, model) as deployment:
        us, india, system, api = deployment.us, deployment.india, deployment.system, deployment.api
        speech = deployment.speech
        american = await a_user(system, preferences=call_handling())
        indian = await api.sign_in(IN_USERS_LINE)
        await api.configure(indian, call_handling())

        # Setup tells each of them the number in their own country.
        for account, line in ((american, US_LINE), (indian, IN_LINE)):
            profile = await api.http.get("/v1/me", headers=account.headers)
            assert profile.json()["call_forwarding"] == {"number": line.value}

        us.answering[USERS_LINE] = Answering.ANSWERS
        india.answering[IN_USERS_LINE] = Answering.ANSWERS
        # The Indian call arrives once the American one has its session; both stay live.
        await us.place_call("CAsim-us-call", CALLER, forwarded_from=american.number)
        await system.reaches(american, "CAsim-us-call", CallState.AGENT_HANDLING)
        await eventually(lambda: len(speech.sessions) == 1, seconds=PATIENCE_SECONDS)
        await india.place_call("CAsim-in-call", IN_CALLER, forwarded_from=indian.number)
        await system.reaches(indian, "CAsim-in-call", CallState.AGENT_HANDLING)
        assert system.orchestrator.live_calls == 2

        # Each caller asks for the user in turn, so each judgement is the one scripted.
        await eventually(lambda: len(speech.sessions) == 2, seconds=PATIENCE_SECONDS)
        first, second = speech.sessions
        await first.emit(said("Is she there?"))
        await system.joined_by_the_user(american, "CAsim-us-call")
        await second.emit(said("Is he there?"))
        await system.joined_by_the_user(indian, "CAsim-in-call")
        assert system.orchestrator.live_calls == 2

        # Each user was rung from the number in their own country, by their own line alone.
        assert [(leg.from_, leg.to) for leg in us.legs.values() if leg.to == USERS_LINE] == [
            (US_LINE.value, USERS_LINE)
        ]
        assert [(leg.from_, leg.to) for leg in india.legs.values() if leg.to == IN_USERS_LINE] == [
            (IN_LINE.value, IN_USERS_LINE)
        ]
        assert not any(leg.to == IN_USERS_LINE for leg in us.legs.values())
        assert not any(leg.to == USERS_LINE for leg in india.legs.values())

        await us.caller_hangs_up("CAsim-us-call")
        await india.caller_hangs_up("CAsim-in-call")
        us_detail = await system.ended(american, "CAsim-us-call")
        in_detail = await system.ended(indian, "CAsim-in-call")

        assert (us_detail["outcome"], in_detail["outcome"]) == (
            "handed_to_user",
            "handed_to_user",
        )
        assert model.unused_steps == 0
        await deployment.released()


async def test_an_indian_user_forwards_to_the_india_line_and_is_rung_from_its_dial_from(
    database: str, pushes: Pushes
) -> None:
    # India's toll-free numbers take calls but cannot place them to Indian phones (D-044).
    model = ScriptedModel([WANTS_THE_USER])
    lines = f"{LINES};dial_from={IN_DIALS_FROM.value}"
    async with two_lines(database, lines, model) as deployment:
        india, system, api = deployment.india, deployment.system, deployment.api
        indian = await api.sign_in(IN_USERS_LINE)
        await api.configure(indian, call_handling())
        profile = await api.http.get("/v1/me", headers=indian.headers)
        assert profile.json()["call_forwarding"] == {"number": IN_LINE.value}

        india.answering[IN_USERS_LINE] = Answering.ANSWERS
        await india.place_call("CAsim-in-call", IN_CALLER, forwarded_from=indian.number)
        await system.reaches(indian, "CAsim-in-call", CallState.AGENT_HANDLING)
        await eventually(lambda: len(deployment.speech.sessions) == 1, seconds=PATIENCE_SECONDS)
        (session,) = deployment.speech.sessions
        await session.emit(said("Is he there?"))
        await system.joined_by_the_user(indian, "CAsim-in-call")

        # The call reached the India line's number; the user's phone showed the US number.
        assert [(leg.from_, leg.to) for leg in india.legs.values() if leg.to == IN_USERS_LINE] == [
            (IN_DIALS_FROM.value, IN_USERS_LINE)
        ]
        assert not deployment.us.legs

        await india.caller_hangs_up("CAsim-in-call")
        detail = await system.ended(indian, "CAsim-in-call")
        assert detail["outcome"] == "handed_to_user"
        assert model.unused_steps == 0
        await deployment.released()
