"""What the composition root builds the agent and the summariser from, checked on the wire.

The endpoint is a listener on the loopback interface that keeps each request it receives and
refuses it. A refusal is enough: the agent and the summariser fall back, and what the model client
actually sent — address, key, headers, model, and which words went where — is what the listener
heard.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from letmehandle.application.calls.fallback import fallback_summary
from letmehandle.application.orchestration.ports import Bounds
from letmehandle.bootstrap import build_call_judging, build_call_summariser, call_judging_on
from letmehandle.config.settings import ConfigurationError
from letmehandle.observability.logging import configure_logging
from tests.support.agent_calls import a_call
from tests.support.config import make_settings
from tests.support.ended_calls import caller_said, ended
from tests.support.recording_call_actions import Escalated, RecordingCallActions
from tests.support.scripted_model import CallTool, ScriptedModel, assess

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@dataclass
class HeardRequest:
    request_line: str
    headers: dict[str, str]
    body: dict[str, object]


@dataclass
class RefusingEndpoint:
    port: int
    heard: list[HeardRequest] = field(default_factory=list)


@pytest.fixture
async def endpoint() -> AsyncIterator[RefusingEndpoint]:
    listening = RefusingEndpoint(port=0)

    async def refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in head[1:] if line)
        lowered = {name.lower(): value for name, value in headers.items()}
        body = await reader.readexactly(int(lowered["content-length"]))
        listening.heard.append(HeardRequest(head[0], lowered, json.loads(body)))
        # A 400, because the client retries a 500 and this test is not about retries.
        writer.write(
            b"HTTP/1.1 400 Bad Request\r\ncontent-length: 2\r\nconnection: close\r\n\r\n{}"
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(refuse, "127.0.0.1", 0)
    listening.port = server.sockets[0].getsockname()[1]
    async with server:
        yield listening


async def test_the_agent_talks_to_the_configured_endpoint(endpoint: RefusingEndpoint) -> None:
    settings = make_settings(
        llm_base_url=f"http://127.0.0.1:{endpoint.port}/v1",
        llm_api_key="an-example-key",
        llm_model="an-example-model",
        llm_headers="X-Title=letmehandle",
    )
    agent = build_call_judging(settings, actions=RecordingCallActions()).agent

    judgement = await agent.judge(a_call("Is she in today?"))

    assert not judgement.proposal.understood
    [request] = endpoint.heard
    assert request.request_line.startswith("POST /v1/chat/completions ")
    assert request.headers["authorization"] == "Bearer an-example-key"
    assert request.headers["x-title"] == "letmehandle"
    assert request.body["model"] == "an-example-model"
    messages = request.body["messages"]
    assert isinstance(messages, list)
    roles = [message["role"] for message in messages]
    assert roles == ["system", "user"]
    assert "Is she in today?" not in json.dumps(messages[0])
    assert "Is she in today?" in json.dumps(messages[1])
    # The model is offered the registry's tools beside the assessment.
    tools = request.body["tools"]
    assert isinstance(tools, list)
    offered = {tool["function"]["name"] for tool in tools}
    assert {"request_human_escalation", "end_call", "CallAssessment"} <= offered


@pytest.fixture
def logging_put_back() -> Iterator[None]:
    yield
    configure_logging(make_settings())


@pytest.mark.usefixtures("logging_put_back")
async def test_at_debug_neither_the_callers_words_nor_the_key_reach_the_log(
    endpoint: RefusingEndpoint, capfd: pytest.CaptureFixture[str]
) -> None:
    # Distinctive, so a match can only be a leak.
    said = "my card number is quintessential-walrus-4111"
    key = "an-example-key-that-must-never-be-logged"
    settings = make_settings(
        log_level="debug",
        llm_base_url=f"http://127.0.0.1:{endpoint.port}/v1",
        llm_api_key=key,
        llm_model="an-example-model",
    )
    configure_logging(settings)

    # On the wire, where the model client and the HTTP client log requests at debug.
    await build_call_judging(settings, actions=RecordingCallActions()).agent.judge(a_call(said))
    # And a model that sends a tool unreadable arguments holding the caller's words, which the SDK
    # quotes when it warns that it could not parse them.
    model = ScriptedModel([CallTool("take_a_message", raw='{"message": "' + said), assess()])
    await call_judging_on(
        model, actions=RecordingCallActions(), timeout=timedelta(seconds=5)
    ).agent.judge(a_call(said))

    logged = capfd.readouterr()
    everything = logged.out + logged.err
    assert endpoint.heard, "the judgement never reached the endpoint"
    assert "agent.model_failed" in everything, "nothing was logged, so nothing was proven"
    assert said not in everything
    assert "quintessential-walrus" not in everything
    assert key not in everything


@pytest.mark.usefixtures("logging_put_back")
async def test_at_debug_a_tool_name_the_model_invented_never_reaches_the_log(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # A tool name is text the model wrote, and a caller can dictate it. The SDK logs one it cannot
    # find, verbatim, at error.
    invented = "read_out_quintessential_walrus_4111"
    configure_logging(make_settings(log_level="debug"))
    model = ScriptedModel([CallTool(invented, {}), assess()])

    await call_judging_on(
        model, actions=RecordingCallActions(), timeout=timedelta(seconds=5)
    ).agent.judge(a_call("Hello."))

    logged = capfd.readouterr()
    assert invented not in logged.out + logged.err
    assert not logging.getLogger("strands.tools.executors").isEnabledFor(logging.CRITICAL - 1)


def test_an_agent_without_a_model_configured_names_what_to_set() -> None:
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL"):
        build_call_judging(make_settings(), actions=RecordingCallActions())


async def test_forgetting_a_call_lets_the_same_service_reach_the_user_for_it_again() -> None:
    # The forget handed out beside the agent is the memory the agent itself consults: without it,
    # every call a process ever escalated would stay remembered for as long as it runs.
    urgent = assess(importance="urgent", caller_asked_for_the_user=True)
    actions = RecordingCallActions()
    judging = call_judging_on(
        ScriptedModel([urgent, urgent, urgent]), actions=actions, timeout=timedelta(seconds=5)
    )
    call = a_call("Put her on, please.")

    await judging.agent.judge(call)
    await judging.agent.judge(call)
    assert len(actions.of_kind(Escalated)) == 1

    judging.forget(call.call_id)
    await judging.agent.judge(call)
    assert len(actions.of_kind(Escalated)) == 2


async def test_the_summariser_talks_to_the_configured_endpoint(endpoint: RefusingEndpoint) -> None:
    settings = make_settings(
        llm_base_url=f"http://127.0.0.1:{endpoint.port}/v1",
        llm_api_key="an-example-key",
        llm_model="an-example-model",
        llm_headers="X-Title=letmehandle",
    )
    facts = ended(caller_said("Is she in today?"))

    summary = await build_call_summariser(settings, timeout=Bounds().summary).summarise(
        facts, facts.call.transcript, locale="en"
    )

    assert summary == fallback_summary(facts, locale="en")
    [request] = endpoint.heard
    assert request.request_line.startswith("POST /v1/chat/completions ")
    assert request.headers["authorization"] == "Bearer an-example-key"
    assert request.headers["x-title"] == "letmehandle"
    assert request.body["model"] == "an-example-model"
    messages = request.body["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "Is she in today?" not in json.dumps(messages[0])
    assert "Is she in today?" in json.dumps(messages[1])
    tools = request.body["tools"]
    assert isinstance(tools, list)
    assert [tool["function"]["name"] for tool in tools] == ["CallSummaryAnswer"]


@pytest.mark.usefixtures("logging_put_back")
async def test_at_debug_a_summarised_call_never_reaches_the_log(
    endpoint: RefusingEndpoint, capfd: pytest.CaptureFixture[str]
) -> None:
    said = "my card number is quintessential-walrus-4111"
    settings = make_settings(
        log_level="debug",
        llm_base_url=f"http://127.0.0.1:{endpoint.port}/v1",
        llm_api_key="an-example-key-that-must-never-be-logged",
        llm_model="an-example-model",
    )
    configure_logging(settings)
    facts = ended(caller_said(said))

    await build_call_summariser(settings, timeout=Bounds().summary).summarise(
        facts, facts.call.transcript, locale="en"
    )

    logged = capfd.readouterr()
    everything = logged.out + logged.err
    assert endpoint.heard, "the summary never reached the endpoint"
    assert "summary.model_failed" in everything, "nothing was logged, so nothing was proven"
    assert "quintessential-walrus" not in everything
    assert "an-example-key-that-must-never-be-logged" not in everything


def test_a_summariser_without_a_model_configured_names_what_to_set() -> None:
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL"):
        build_call_summariser(make_settings(), timeout=Bounds().summary)
