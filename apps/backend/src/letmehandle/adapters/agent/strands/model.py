"""The model the agent runs on, reached as an OpenAI-compatible endpoint (D-007).

The only model this adapter constructs. A hosted API, an aggregator and a server on somebody's own
machine are the same few values here, so choosing between them is configuration and never code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.models.openai import OpenAIModel
from strands.tools.executors import SequentialToolExecutor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from strands.hooks import HookProvider
    from strands.models.model import Model
    from strands.tools import PythonAgentTool

    from letmehandle.config.settings import LLMEndpoint


def openai_compatible_model(endpoint: LLMEndpoint) -> OpenAIModel:
    """A model at `endpoint`, sending its key as a bearer token and any extra headers alongside."""
    return OpenAIModel(
        client_args={
            "base_url": endpoint.base_url,
            "api_key": endpoint.api_key,
            "default_headers": dict(endpoint.headers),
        },
        model_id=endpoint.model,
    )


def single_use_agent(
    model: Model,
    *,
    system_prompt: str,
    tools: Sequence[PythonAgentTool] = (),
    hooks: Sequence[HookProvider] = (),
) -> Agent:
    """An SDK agent for one request: silent, untrimmed, unretried, running tools in order."""
    return Agent(
        model=model,
        tools=list(tools),
        hooks=list(hooks),
        system_prompt=system_prompt,
        # The default handler prints the streamed call to stdout.
        callback_handler=None,
        # The default manager silently drops the start of an overflowing conversation.
        conversation_manager=NullConversationManager(),
        tool_executor=SequentialToolExecutor(),
        # Retries belong inside the caller's time bound.
        retry_strategy=None,
    )
