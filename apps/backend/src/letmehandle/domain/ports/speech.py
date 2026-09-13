"""Realtime spoken conversation over an audio source and sink, unaware of calls (D-006)."""

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
    """What a speech provider can do; `barge_in` is whether the caller can interrupt it."""

    barge_in: bool = False
    context_updates_mid_session: bool = False
    reconnection: bool = False
    languages: tuple[str, ...] = ()
    input_formats: tuple[AudioFormat, ...] = ()
    output_format: AudioFormat | None = None

    def speaks(self, locale: str) -> bool:
        """Whether any declared language shares the locale's base language."""
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
    """Words from either side, partial until `is_final`."""

    text: str
    speaker_is_caller: bool
    is_final: bool

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantError("an empty transcript fragment is not a fragment")


@dataclass(frozen=True, slots=True)
class SpeechStarted(SpeechEvent):
    """Somebody began speaking, the signal barge-in is built on."""

    by_caller: bool


@dataclass(frozen=True, slots=True)
class SpeechEnded(SpeechEvent):
    """They stopped."""

    by_caller: bool


@dataclass(frozen=True, slots=True)
class SessionFailed(SpeechEvent):
    """The session cannot continue, reported as an event in the stream rather than raised."""

    reason: str
    retryable: bool


class SpeechSession(ABC):
    """One live conversation, releasing all it holds on every exit path, cancellation included."""

    @abstractmethod
    async def send_audio(self, frame: AudioFrame) -> None:
        """Push caller audio in."""

    @abstractmethod
    def events(self) -> AsyncIterator[SpeechEvent]:
        """Everything the model produces, in order, through a bounded queue."""

    @abstractmethod
    async def update_context(self, context: str) -> None:
        """Change what the model knows, without reconnecting, as when the user joins the call."""

    @abstractmethod
    async def interrupt(self) -> None:
        """Stop the model speaking now, and discard what it had queued."""

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
        greeting: str,
        locale: str,
        input_format: AudioFormat,
    ) -> SpeechSession:
        """Open a session in `locale` that opens with `greeting`, or raise `ProviderError`."""

    def supported_locales(self) -> Sequence[str]:
        return self.capabilities.languages
