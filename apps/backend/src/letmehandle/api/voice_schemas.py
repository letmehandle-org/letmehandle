"""What the voice API returns.

The catalogue and the capabilities travel together on purpose. A client that bundles its own
list of voices is a client that shows one the server cannot use; a client that guesses at what
a provider can do is a client that offers a control which does nothing. Both are answered by
the server describing itself.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from letmehandle.api.schemas import Request, Response


class VoiceCapabilitiesPayload(Response):
    """What the configured provider can do.

    The interface renders from these. Everything absent means the control is not drawn at all —
    not disabled, not labelled as unavailable — because a control that cannot work teaches
    people to distrust the ones that can.
    """

    builtin_voices: bool
    preview: bool
    custom_voice: bool
    cloning: bool
    local_inference: bool
    realtime_streaming: bool


class VoicePayload(Response):
    """One voice, and whether this one in particular can be heard.

    Per voice rather than per provider: a provider holding a sample for one voice and not
    another declares the capability and can still serve only the one, and a client drawing a
    control from the capability alone draws two that fail.
    """

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
    """What the user has chosen, and what a call would actually use.

    `resolved_voice_id` is the answer the fallback chain gives right now — the cloned voice if
    it is still available, otherwise the chosen one, otherwise the provider's default. It is
    returned alongside the choice because those differ exactly when something has gone wrong
    with a voice, and that is the moment a user should be able to see it.
    """

    cloned_voice_id: str | None
    persona_voice_id: str | None
    resolved_voice_id: str


class VoiceSelectionUpdate(Request):
    """A change to the chosen voice.

    Both fields are optional and `null` clears: a user removing their choice returns to the
    provider's default, which is a thing they must be able to do.
    """

    persona_voice_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
