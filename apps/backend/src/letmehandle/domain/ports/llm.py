"""The model that makes judgements.

Separate from the speech provider (they are different concerns with different failure modes),
and expressed as an OpenAI-compatible shape, because that one shape covers hosted services,
aggregators, dedicated inference providers and a server somebody runs on their own machine.
A self-hoster needs no code change and no vendor account.

Structured output is a first-class method rather than a convention. Every decision the agent
makes is a typed object, and parsing one out of prose is where reliability goes to die.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")


class Role(StrEnum):
    """Who a message is from.

    `USER` here means the model's interlocutor, which in this product is the transcript of a
    call rather than the person who owns the account. That distinction matters: content in a
    USER message arrives from an unknown caller and is data, never instruction.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn of the conversation the model sees."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise InvariantError("an empty message tells the model nothing and costs tokens")


@dataclass(frozen=True, slots=True)
class LLMCapabilities:
    """What a configured model can do.

    Whether a model supports structured output decides whether the agent can rely on it for
    decisions at all, which is the difference between a model that can run this product and
    one that can only chat.
    """

    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    max_context_tokens: int = 0


class LLMProvider(ABC):
    """Any OpenAI-compatible endpoint: hosted, aggregated, or local."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this provider is called, for logs and metrics. Never the API key."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Which model is configured."""

    @property
    @abstractmethod
    def capabilities(self) -> LLMCapabilities:
        """What it can do."""

    @abstractmethod
    async def complete(self, messages: Sequence[Message]) -> str:
        """Free text. For anything a person reads rather than anything code branches on."""

    @abstractmethod
    async def complete_structured(self, messages: Sequence[Message], schema: type[T]) -> T:
        """A typed object, validated before it is returned.

        An implementation must raise rather than return something half-parsed. A decision built
        from a partially understood response is worse than no decision: the caller cannot tell
        the difference, and acts on it.
        """
