"""The voices a provider offers and declares it can do, apart from the speech provider (D-009)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.domain.models.voice import VoiceSelection


@dataclass(frozen=True, slots=True)
class VoiceCapabilities:
    """What a voice provider can do, each false unless declared."""

    builtin_voices: bool = False
    preview: bool = False
    custom_voice: bool = False
    cloning: bool = False
    local_inference: bool = False
    realtime_streaming: bool = False


@dataclass(frozen=True, slots=True)
class Voice:
    """One voice a provider offers."""

    id: str
    name: str
    locales: tuple[str, ...]
    # Whether this particular voice has a sample to listen to.
    previewable: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise InvariantError("a voice needs an identifier and a name")
        if not self.locales:
            raise InvariantError("a voice that speaks no language cannot be selected for any user")

    def speaks(self, locale: str) -> bool:
        """Whether this voice covers the locale, so a voice listed for `en` serves `en-GB`."""
        return any(locale == known or locale.startswith(f"{known}-") for known in self.locales)


@dataclass(frozen=True, slots=True)
class VoiceSample:
    """A short recording of a voice to listen to before choosing it, with its media type."""

    audio: bytes
    media_type: str

    def __post_init__(self) -> None:
        if not self.audio:
            raise InvariantError("a sample with no audio in it is silence, not a preview")
        if not self.media_type.strip():
            raise InvariantError("a sample must say what format it is in")


class VoiceProvider(ABC):
    """Supplies the voices the assistant can speak with."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this provider is called."""

    @property
    @abstractmethod
    def capabilities(self) -> VoiceCapabilities:
        """What it can do, which callers read instead of the provider's name."""

    @property
    @abstractmethod
    def default_voice_id(self) -> str:
        """The voice used when the user has expressed no preference."""

    @abstractmethod
    async def list_voices(self, locale: str | None = None) -> Sequence[Voice]:
        """The voices on offer, optionally narrowed to a language."""

    @abstractmethod
    async def preview(self, voice_id: str) -> VoiceSample:
        """A sample, or `CapabilityNotSupportedError`, or a domain error for an unknown voice."""

    @abstractmethod
    async def is_available(self, voice_id: str) -> bool:
        """Whether this voice can be used right now, which a revoked cloned voice cannot."""


async def resolve_voice(provider: VoiceProvider, selection: VoiceSelection, *, locale: str) -> str:
    """The first available of the cloned, persona, default or any voice in `locale` (D-039)."""
    # A cloned voice is the user's own, so it is not held to the locale.
    cloned = selection.cloned_voice_id
    if cloned is not None and await provider.is_available(cloned):
        return cloned

    if not locale.strip():
        raise InvariantError("a locale is required to choose a voice for a call")
    speaking = [voice.id for voice in await provider.list_voices(locale)]
    default = provider.default_voice_id
    preferred = [selection.persona_voice_id, default, *speaking]
    for candidate in preferred:
        if candidate in speaking and await provider.is_available(candidate):
            return candidate
    # The default even in another language, because a call must never be silent.
    return default
