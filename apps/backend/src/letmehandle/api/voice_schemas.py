"""What the voice API returns: the catalogue together with the provider's capabilities."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from letmehandle.api.schemas import Request, Response


class VoiceCapabilitiesPayload(Response):
    """What the configured provider can do; a control it cannot serve is not drawn."""

    builtin_voices: bool
    preview: bool
    custom_voice: bool
    cloning: bool
    local_inference: bool
    realtime_streaming: bool


class VoicePayload(Response):
    """One voice, and whether this one in particular can be heard."""

    id: str
    name: str
    locales: list[str]
    previewable: bool


class VoiceCatalogueResponse(Response):
    """Everything a client needs to draw the voice screen."""

    provider: str
    capabilities: VoiceCapabilitiesPayload
    default_voice_id: str
    voices: list[VoicePayload]


class VoiceSelectionResponse(Response):
    """What the user has chosen, and the voice the fallback chain resolves to now."""

    cloned_voice_id: str | None
    persona_voice_id: str | None
    resolved_voice_id: str


class VoiceSelectionUpdate(Request):
    """A change to the chosen voice: required, with `null` returning to the provider's default."""

    persona_voice_id: Annotated[str, Field(min_length=1, max_length=64)] | None
