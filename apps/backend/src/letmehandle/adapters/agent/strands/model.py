"""The model the agent runs on, reached as an OpenAI-compatible endpoint (D-007).

The only model this adapter constructs. A hosted API, an aggregator and a server on somebody's own
machine are the same few values here, so choosing between them is configuration and never code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from strands.models.openai import OpenAIModel

if TYPE_CHECKING:
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
