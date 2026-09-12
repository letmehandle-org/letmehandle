"""A fixed catalogue of voices, and nothing it cannot back up.

The catalogue arrives through the constructor rather than being written into the class, so a
deployment that has licensed different voices changes a configuration value instead of this
file. `SHIPPED_VOICES` is what the application builds when it has been told nothing.

The declaration is the part that matters. Cloning and custom voices are false permanently, not
pending: nothing in this project can train a voice, and a true there renders a training flow in
front of somebody it will fail for — the exact outcome D-009 exists to prevent. Preview follows
the sample audio, because no speech model ships until a later phase and a preview control with
nothing to play is a button that produces silence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    InvariantError,
    ProviderError,
)
from letmehandle.domain.ports.voice import Voice, VoiceCapabilities, VoiceProvider

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The voices every installation gets. Small on purpose: each one is a licence and an asset, and
# a catalogue listing voices the deployment does not hold is a selection screen that ends in a
# call nobody can hear. English only, which is what the first release supports.
SHIPPED_VOICES: Final[tuple[Voice, ...]] = (
    Voice(id="ava", name="Ava", locales=("en",)),
    Voice(id="noah", name="Noah", locales=("en",)),
    Voice(id="iris", name="Iris", locales=("en",)),
)

SHIPPED_DEFAULT_VOICE_ID: Final = "ava"


class BuiltInVoiceProvider(VoiceProvider):
    """The voices this installation owns outright."""

    def __init__(
        self,
        voices: Sequence[Voice],
        *,
        default_voice_id: str,
        samples: Mapping[str, bytes] | None = None,
    ) -> None:
        if not voices:
            raise InvariantError("a voice provider with an empty catalogue can never be spoken to")

        catalogue = tuple(voices)
        known = {voice.id for voice in catalogue}
        if len(known) != len(catalogue):
            # Two rows claiming the same identifier make every lookup below answer at random,
            # including the one that decides which sample a caller hears.
            raise InvariantError("two voices in the catalogue share an identifier")
        if default_voice_id not in known:
            # The fallback chain ends here. A default that is not in the catalogue turns a call
            # with no expressed preference into a call with no voice.
            raise InvariantError(
                f"the default voice {default_voice_id!r} is not in the catalogue, which would "
                "leave a call with no voice at all"
            )

        self._samples = dict(samples or {})
        unknown_samples = sorted(set(self._samples) - known)
        if unknown_samples:
            # Sample audio for a voice nobody can select is a licensing mistake or a typo in the
            # identifier, and the second one silently costs the voice it was meant for.
            raise InvariantError(
                f"sample audio was supplied for voices outside the catalogue: {unknown_samples}"
            )

        self._voices = catalogue
        self._known = known
        self._default_voice_id = default_voice_id

    @property
    def name(self) -> str:
        return "builtin"

    @property
    def capabilities(self) -> VoiceCapabilities:
        return VoiceCapabilities(
            builtin_voices=True,
            # True only where there is audio to serve, so nothing downstream offers a control
            # that would play silence.
            preview=bool(self._samples),
            # Permanently false, both of them. Neither a catalogue nor anything else in this
            # product can train a voice, and declaring otherwise renders a flow that cannot work.
            custom_voice=False,
            cloning=False,
            # A catalogue is a list of files. It runs no model here and streams nothing.
            local_inference=False,
            realtime_streaming=False,
        )

    @property
    def default_voice_id(self) -> str:
        return self._default_voice_id

    async def list_voices(self, locale: str | None = None) -> Sequence[Voice]:
        if locale is None:
            return self._voices
        return tuple(voice for voice in self._voices if voice.speaks(locale))

    async def is_available(self, voice_id: str) -> bool:
        # A built-in voice cannot be revoked or expire the way a cloned one can, so membership
        # of the catalogue is the whole answer.
        return voice_id in self._known

    async def sample_audio(self, voice_id: str) -> bytes:
        """The audio a preview control plays for one voice.

        Raises rather than returning nothing, and raises two different things: a provider with
        no samples cannot do this at all, which callers branch on; anything else is a failure to
        find the audio for one voice. A `KeyError` here would reach the caller as a crash in
        something that reads like a lookup.
        """
        if not self._samples:
            raise CapabilityNotSupportedError(self.name, "preview")
        if voice_id not in self._known:
            raise ProviderError(self.name, f"no voice named {voice_id!r}", retryable=False)
        try:
            return self._samples[voice_id]
        except KeyError:
            # A catalogue voice can be sampled while its neighbour is not, so the capability
            # flag is true and this particular preview still has nothing to play.
            raise ProviderError(
                self.name, f"no sample audio for voice {voice_id!r}", retryable=False
            ) from None


def built_in_voice_provider() -> BuiltInVoiceProvider:
    """The provider the application builds when nothing has been configured.

    A function rather than an instance so the catalogue and its default are paired in one place;
    two constants exported separately eventually get combined wrongly.
    """
    return BuiltInVoiceProvider(SHIPPED_VOICES, default_voice_id=SHIPPED_DEFAULT_VOICE_ID)
