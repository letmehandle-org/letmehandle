"""A configured catalogue of voices that never declares cloning or custom voices (D-009)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    InvariantError,
    ProviderError,
)
from letmehandle.domain.ports.voice import (
    Voice,
    VoiceCapabilities,
    VoiceProvider,
    VoiceSample,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class BuiltInVoiceProvider(VoiceProvider):
    """The voices this installation owns outright."""

    def __init__(
        self,
        voices: Sequence[Voice],
        *,
        default_voice_id: str,
        samples: Mapping[str, VoiceSample] | None = None,
    ) -> None:
        if not voices:
            raise InvariantError("a voice provider with an empty catalogue can never be spoken to")

        catalogue = tuple(voices)
        known = {voice.id for voice in catalogue}
        if len(known) != len(catalogue):
            raise InvariantError("two voices in the catalogue share an identifier")
        if default_voice_id not in known:
            raise InvariantError(
                f"the default voice {default_voice_id!r} is not in the catalogue, which would "
                "leave a call with no voice at all"
            )

        self._samples = dict(samples or {})
        unknown_samples = sorted(set(self._samples) - known)
        if unknown_samples:
            raise InvariantError(
                f"sample audio was supplied for voices outside the catalogue: {unknown_samples}"
            )

            # A voice is previewable only when it has a sample of its own.
        self._voices = tuple(
            replace(voice, previewable=voice.id in self._samples) for voice in catalogue
        )
        self._known = known
        self._default_voice_id = default_voice_id

    @property
    def name(self) -> str:
        return "builtin"

    @property
    def capabilities(self) -> VoiceCapabilities:
        return VoiceCapabilities(
            builtin_voices=True,
            preview=bool(self._samples),
            # Nothing in this product can train a voice (D-009).
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
        return voice_id in self._known

    async def preview(self, voice_id: str) -> VoiceSample:
        """One voice's sample; unsupported with no samples, a provider error without this one."""
        if not self._samples:
            raise CapabilityNotSupportedError(self.name, "preview")
        if voice_id not in self._known:
            raise ProviderError(self.name, f"no voice named {voice_id!r}", retryable=False)
        try:
            return self._samples[voice_id]
        except KeyError:
            raise ProviderError(
                self.name, f"no sample audio for voice {voice_id!r}", retryable=False
            ) from None
