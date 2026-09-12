"""What the composition root builds the agent from, checked on the wire.

The endpoint is a listener on the loopback interface that keeps each request it receives and
refuses it. A refusal is enough: the agent falls back, and what the model client actually sent —
address, key, headers, model, and which words went where — is what the listener heard.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from letmehandle.bootstrap import build_call_agent
from letmehandle.config.settings import ConfigurationError
from tests.support.agent_calls import a_call, decide_by_policy
from tests.support.config import make_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
    agent = build_call_agent(settings, tools=[], consider=decide_by_policy)

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


def test_an_agent_without_a_model_configured_names_what_to_set() -> None:
    with pytest.raises(ConfigurationError, match="LLM_BASE_URL"):
        build_call_agent(make_settings(), tools=[], consider=decide_by_policy)
