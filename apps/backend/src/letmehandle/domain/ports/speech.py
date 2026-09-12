"""Realtime spoken conversation.

Deliberately unaware of calls. One of the two transports this product supports cannot supply
call audio at all, so a speech layer that assumed a call would become a platform branch
somewhere else. It consumes an audio source and writes to a sink; whether those are a phone
call, a microphone or a file is not its business.

A session is an async context manager, so the only way to open one without closing it is to
write code that would not survive review.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType

    from letmehandle.domain.models.audio import AudioFormat, AudioFrame


@dataclass(frozen=True, slots=True)
class SpeechCapabilities:
    """What a speech provider can do.

    `barge_in` is the one that decides whether the conversation feels like a conversation: a
    provider that cannot be interrupted talks over the caller, and callers hang up on that.
    """

    barge_in: bool = False
    context_updates_mid_session: bool = False
    reconnection: bool = False
    languages: tuple[str, ...] = ()
    input_formats: tuple[AudioFormat, ...] = ()
    output_format: AudioFormat | None = None

    def speaks(self, locale: str) -> bool:
        base = locale.split("-")[0]
        return any(known == locale or known.split("-")[0] == base for known in self.languages)


@dataclass(frozen=True, slots=True)
class SpeechEvent:
    """Base for everything a session emits. Never constructed directly."""


@dataclass(frozen=True, slots=True)
class AudioProduced(SpeechEvent):
    """The model said something."""

    frame: AudioFrame


@dataclass(frozen=True, slots=True)
class TranscriptProduced(SpeechEvent):
    """Words, from either side.

    `is_final` distinguishes a partial recognition from a settled one. Acting on a partial is
    how an assistant answers a question the caller had not finished asking.
    """

    text: str
    speaker_is_caller: bool
    is_final: bool

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantError("an empty transcript fragment is not a fragment")


@dataclass(frozen=True, slots=True)
class SpeechStarted(SpeechEvent):
    """Somebody began speaking. The signal barge-in is built on."""

    by_caller: bool


@dataclass(frozen=True, slots=True)
class SpeechEnded(SpeechEvent):
    """They stopped."""

    by_caller: bool


@dataclass(frozen=True, slots=True)
class SessionFailed(SpeechEvent):
    """The session cannot continue.

    An event rather than an exception, because it arrives while a caller is iterating the
    stream, and a raised exception there is a resource leak waiting for somebody to forget a
    `finally`.
    """

    reason: str
    retryable: bool


class SpeechSession(ABC):
    """One live conversation.

    Opened through `SpeechProvider.connect`, and closed by leaving its context. Everything it
    holds — the connection, the queues, the tasks — is released on every exit path, including
    cancellation, which is the path most often left out.
    """

    @abstractmethod
    async def send_audio(self, frame: AudioFrame) -> None:
        """Push caller audio in."""

    @abstractmethod
    def events(self) -> AsyncIterator[SpeechEvent]:
        """Everything the model produces, in order.

        Bounded internally. An unbounded queue turns a slow consumer into an out-of-memory
        failure rather than a visible backpressure error, and the slow consumer is the normal
        case when something downstream is struggling.
        """

    @abstractmethod
    async def update_context(self, context: str) -> None:
        """Change what the model knows, without reconnecting.

        Required for escalation: when the user joins, the model has to be told that the person
        it was speaking for is now on the call.
        """

    @abstractmethod
    async def interrupt(self) -> None:
        """Stop the model speaking, now, and discard what it had queued.

        Discarding the queue is the part that gets missed, and missing it is why interruption
        feels broken: the model stops producing, then plays out everything it already made.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release everything. Safe to call more than once."""

    async def __aenter__(self) -> SpeechSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()


class SpeechProvider(ABC):
    """Opens speech sessions."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this provider is called."""

    @property
    @abstractmethod
    def capabilities(self) -> SpeechCapabilities:
        """What it can do."""

    @abstractmethod
    async def connect(
        self,
        *,
        system_context: str,
        voice_id: str,
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        """Open a session, or raise `ProviderError`.

        The input format is supplied by the caller rather than assumed, because the audio comes
        from a transport whose format is not this provider's choice. A provider that cannot
        accept it converts, or says so.
        """

    def supported_locales(self) -> Sequence[str]:
        return self.capabilities.languages
