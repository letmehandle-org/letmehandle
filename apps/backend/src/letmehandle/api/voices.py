"""Choosing how the assistant sounds.

The one thing worth reading twice: which routes exist depends on what the configured provider
can do. A provider that cannot preview leaves this module registering no preview route at all,
so asking for one is a 404 from the router rather than a failure from inside an adapter that
was never going to work.

That is a deliberate choice over the easier alternative — registering everything and refusing
at the handler. A route that exists and always refuses still appears in the schema, still
generates a client method, and still gets a button drawn for it somewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Response, status

from letmehandle.api.dependencies import (
    CurrentUser,
    Preferences,
    Voices,
    get_current_user,
)
from letmehandle.api.errors import UNPROCESSABLE, ApiError
from letmehandle.api.voice_schemas import (
    VoiceCapabilitiesPayload,
    VoiceCatalogueResponse,
    VoicePayload,
    VoiceSelectionResponse,
    VoiceSelectionUpdate,
)
from letmehandle.application.preferences.service import PersonaVoice, PreferenceChanges
from letmehandle.domain.errors import DomainError
from letmehandle.domain.ports.voice import resolve_voice

if TYPE_CHECKING:
    from letmehandle.domain.models.voice import VoiceSelection
    from letmehandle.domain.ports.voice import VoiceProvider

# Every route here is a signed-in one. Declared on the routes rather than taken as an
# argument they do not use, so that adding a route without deciding this is not possible.
#
# The user, not the token: a token outlives the account it names, and a deleted account
# reading the catalogue for the rest of an access token's life is a weaker answer than the
# one every other route in this API gives.
SIGNED_IN = [Depends(get_current_user)]


def build_voice_router(provider: VoiceProvider) -> APIRouter:
    """The voice routes this provider can actually serve.

    Built from the provider rather than declared once, because the answer differs between
    deployments and the schema should say so. A client reading the generated schema learns what
    exists here, which is the same thing the interface renders from.
    """
    router = APIRouter(prefix="/v1", tags=["voice"])
    _add_always(router)

    if provider.capabilities.preview:
        _add_preview(router)

    return router


def _add_always(router: APIRouter) -> None:
    @router.get(
        "/voices",
        response_model=VoiceCatalogueResponse,
        summary="The voices on offer",
        dependencies=SIGNED_IN,
    )
    async def read_catalogue(voices: Voices) -> VoiceCatalogueResponse:
        """What the configured provider offers, and what it can do with it.

        The client renders from this rather than from a bundled list, so a deployment that
        changes provider changes the screen without shipping an app.

        Behind a token like everything else: this is a signed-in screen, and which provider a
        deployment runs is not something to tell whoever asks.
        """
        catalogue = await voices.list_voices()
        capabilities = voices.capabilities
        return VoiceCatalogueResponse(
            provider=voices.name,
            default_voice_id=voices.default_voice_id,
            capabilities=VoiceCapabilitiesPayload(
                builtin_voices=capabilities.builtin_voices,
                preview=capabilities.preview,
                custom_voice=capabilities.custom_voice,
                cloning=capabilities.cloning,
                local_inference=capabilities.local_inference,
                realtime_streaming=capabilities.realtime_streaming,
            ),
            voices=[
                VoicePayload(
                    id=voice.id,
                    name=voice.name,
                    locales=list(voice.locales),
                    previewable=voice.previewable,
                )
                for voice in catalogue
            ],
        )

    @router.get(
        "/preferences/voice",
        response_model=VoiceSelectionResponse,
        summary="The chosen voice",
    )
    async def read_selection(
        user: CurrentUser, preferences: Preferences, voices: Voices
    ) -> VoiceSelectionResponse:
        stored = await preferences.get(user.id)
        return await _selection_response(stored.voice, voices, locale=stored.locale)

    @router.put(
        "/preferences/voice",
        response_model=VoiceSelectionResponse,
        summary="Choose a voice",
    )
    async def choose_voice(
        body: VoiceSelectionUpdate,
        user: CurrentUser,
        preferences: Preferences,
        voices: Voices,
    ) -> VoiceSelectionResponse:
        """Set or clear the chosen voice.

        A voice the provider does not offer is refused rather than stored. Storing it would
        mean a selection that silently falls through to the default on every call, which looks
        to the user exactly like their choice being ignored.

        Only the half this request is about is sent. The cloned voice is left to the service to
        carry, under the lock it takes: read here instead, a clone revoked while this request
        was in flight would be written back from a read taken before it.
        """
        if body.persona_voice_id is not None and not await voices.is_available(
            body.persona_voice_id
        ):
            raise ApiError(
                UNPROCESSABLE,
                "invalid_request",
                "That voice is not one this assistant offers.",
            )

        updated = await preferences.apply(
            user.id, PreferenceChanges(persona_voice=PersonaVoice(body.persona_voice_id))
        )
        return await _selection_response(updated.voice, voices, locale=updated.locale)


def _add_preview(router: APIRouter) -> None:
    """Registered only where the provider declares it. See the module docstring."""

    @router.get(
        "/voices/{voice_id}/preview",
        summary="Hear a voice",
        response_class=Response,
        dependencies=SIGNED_IN,
        responses={200: {"content": {"audio/mpeg": {}}}},
    )
    async def preview(voice_id: str, voices: Voices) -> Response:
        # No branch for a provider that declares preview and then refuses: the contract
        # every implementation runs asserts the declaration and the behaviour agree, and a
        # handler for a state a test forbids is a handler nothing can ever exercise.
        try:
            sample = await voices.preview(voice_id)
        except DomainError as error:
            # One answer for both "no such voice" and "that voice has no sample", because
            # both are true statements of the same fact from the caller's side: there is
            # nothing to play. Which voices have something is said in the catalogue, per voice.
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "no_sample",
                "There is nothing to play for that voice.",
            ) from error

        return Response(
            content=sample.audio,
            media_type=sample.media_type,
            # A sample never changes for a given voice, and it is the same bytes for every
            # user, so it is worth caching hard. Private, because which voice somebody is
            # listening to is a thing about them.
            headers={"Cache-Control": "private, max-age=86400"},
        )


async def _selection_response(
    selection: VoiceSelection, voices: VoiceProvider, *, locale: str
) -> VoiceSelectionResponse:
    return VoiceSelectionResponse(
        cloned_voice_id=selection.cloned_voice_id,
        persona_voice_id=selection.persona_voice_id,
        resolved_voice_id=await resolve_voice(voices, selection, locale=locale),
    )
