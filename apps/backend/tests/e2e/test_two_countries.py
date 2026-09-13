"""One deployment serving the US and India, each country's calls on a line of its own (D-041).

Two simulated accounts of one provider stand for the two lines, calling the application back under
their own path prefixes. A US user and an Indian user are each told their own region's number to
forward to; a call forwarded to each line runs at the same time through the one orchestrator; each
caller asks for the user, and each user is dialled from the line their call arrived on — a number
in their own country — joins, and has the call summarised when it ends.
"""

from __future__ import annotations

from dataclasses import replace
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
    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.orchestration.ports import CallJudging
    from tests.e2e.harness import Pushes

pytestmark = pytest.mark.integration

US_LINE: Final = PhoneNumber.parse("+12025550100")
# India's numbers here are shorter than any in India's plan, so they reach nobody.
IN_LINE: Final = PhoneNumber.parse("+91555010")
IN_USERS_LINE: Final = "+91555001"
IN_CALLER: Final = "+91555002"

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


async def test_a_us_call_and_an_indian_call_each_reach_their_user_from_their_own_line(
    database: str, pushes: Pushes
) -> None:
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
        telephony_lines=LINES,
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
    model = ScriptedModel([WANTS_THE_USER, WANTS_THE_USER])
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
            system = System(app, api)
            american = await a_user(system, preferences=call_handling())
            indian = await api.sign_in(IN_USERS_LINE)
            await api.configure(indian, call_handling())

            # Setup tells each of them the number in their own country.
            for account, line in ((american, US_LINE), (indian, IN_LINE)):
                profile = await api.http.get("/v1/me", headers=account.headers)
                assert profile.json()["call_forwarding"] == {"number": line.value}

            us.answering[USERS_LINE] = Answering.ANSWERS
            india.answering[IN_USERS_LINE] = Answering.ANSWERS
            await us.place_call("CAsim-us-call", CALLER, forwarded_from=american.number)
            await india.place_call("CAsim-in-call", IN_CALLER, forwarded_from=indian.number)
            await system.reaches(american, "CAsim-us-call", CallState.AGENT_HANDLING)
            await system.reaches(indian, "CAsim-in-call", CallState.AGENT_HANDLING)
            assert system.orchestrator.live_calls == 2

            # Each caller asks for the user, one after the other, so each judgement is the one
            # the script expects; both calls stay live throughout.
            await eventually(lambda: len(speech.sessions) == 2, seconds=PATIENCE_SECONDS)
            first, second = speech.sessions
            await first.emit(
                TranscriptProduced("Is she there?", speaker_is_caller=True, is_final=True)
            )
            await system.joined_by_the_user(american, "CAsim-us-call")
            await second.emit(
                TranscriptProduced("Is he there?", speaker_is_caller=True, is_final=True)
            )
            await system.joined_by_the_user(indian, "CAsim-in-call")
            assert system.orchestrator.live_calls == 2

            # Each user was rung from the number in their own country, by their own line alone.
            assert [(leg.from_, leg.to) for leg in us.legs.values() if leg.to == USERS_LINE] == [
                (US_LINE.value, USERS_LINE)
            ]
            assert [
                (leg.from_, leg.to) for leg in india.legs.values() if leg.to == IN_USERS_LINE
            ] == [(IN_LINE.value, IN_USERS_LINE)]
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
            for provider, binding in zip((us, india), bindings, strict=True):
                transport = binding.transport
                assert isinstance(transport, TwilioCallTransport)
                await provider.settle(transport)
                assert transport.active_calls == 0
                assert transport.open_media_sockets == 0
            await system.released()
        finally:
            await api.aclose()
            await us.close()
            await india.close()
