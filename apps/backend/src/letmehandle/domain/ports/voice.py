"""How the assistant sounds.

Separate from the speech provider on purpose: the model that holds the conversation and the
system that supplies a voice are not the same concern, and one of them can be replaced without
the other.

Capabilities are declared rather than inferred, and the interface renders from them. A provider
that cannot clone a voice makes the training flow absent — not disabled, not labelled as coming
soon. A control that cannot work teaches people to distrust the ones that can.
"""

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
    """What a voice provider can actually do.

    Everything defaults to false. A provider that forgets to declare something offers less than
    it could, which is recoverable; one that inherits a true it did not mean offers a feature
    that fails in front of a caller.
    """

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
    # Whether this particular voice can be listened to. Per voice rather than per provider,
    # because a provider holding a sample for one voice and not its neighbour declares the
    # capability and can still only serve one of them — and a client told "preview works" draws
    # a control for every voice in the list.
    previewable: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise InvariantError("a voice needs an identifier and a name")
        if not self.locales:
            raise InvariantError("a voice that speaks no language cannot be selected for any user")

    def speaks(self, locale: str) -> bool:
        """Whether this voice covers the locale.

        A prefix match, so that a voice listed for `en` serves a user configured for `en-GB`.
        The alternative is a catalogue that has to enumerate every regional tag, which is a
        catalogue that will be missing one.
        """
        return any(locale == known or locale.startswith(f"{known}-") for known in self.locales)


@dataclass(frozen=True, slots=True)
class VoiceSample:
    """A short recording of a voice, for somebody to listen to before choosing it.

    The media type travels with the bytes. A caller that has to guess produces a response the
    client cannot play, and the guess is wrong the first time a provider returns anything but
    the format that was assumed.
    """

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
        """What it can do. Callers read this rather than the provider's name."""

    @property
    @abstractmethod
    def default_voice_id(self) -> str:
        """The voice used when the user has expressed no preference.

        Every provider has one. A provider with no default would leave a call with no voice,
        which is a silent assistant — the worst possible failure for this product.
        """

    @abstractmethod
    async def list_voices(self, locale: str | None = None) -> Sequence[Voice]:
        """The voices on offer, optionally narrowed to a language."""

    @abstractmethod
    async def preview(self, voice_id: str) -> VoiceSample:
        """A sample of this voice.

        Raises `CapabilityNotSupportedError` when the provider does not declare `preview`, and
        a domain error when the voice is unknown. Never a vendor exception and never a
        `KeyError`: a caller should be able to tell "this provider cannot do that" from "there
        is no such voice", and neither is a server fault.
        """

    @abstractmethod
    async def is_available(self, voice_id: str) -> bool:
        """Whether this voice can be used right now.

        Separate from listing because a cloned voice can be revoked, expire, or fail to
        synthesise long after it was chosen, and the resolution below depends on finding that
        out before the call rather than during it.
        """


async def resolve_voice(provider: VoiceProvider, selection: VoiceSelection, *, locale: str) -> str:
    """Which voice a call in `locale` will actually use.

    The fallback chain, implemented once, in the domain:

        the user's cloned voice
        → the persona voice they chose, if it speaks the language
        → the provider's default, if it speaks the language
        → the first voice in the catalogue that does
        → the provider's default

    Each step falls through when the voice is unavailable, so a revoked or broken custom voice
    produces a call that sounds different rather than a call that does not happen. Silence is
    the one outcome this must never produce.

    A cloned voice is the user's own voice, which speaks whatever they speak, so the language is
    not asked of it. Every other voice is held to the language (D-039): a voice chosen in English
    reading Hindi is a call the caller cannot follow, and a different voice is the lesser change.
    """
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
    return default
