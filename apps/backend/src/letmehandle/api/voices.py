"""Choosing how the assistant sounds, with only the routes the provider can serve (D-024)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Response, status

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
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

# The signed-in user, for routes that take no user argument.
SIGNED_IN = [Depends(get_current_user)]


def build_voice_router(provider: VoiceProvider) -> APIRouter:
    """The voice routes this provider can actually serve."""
    router = APIRouter(
        prefix="/v1", tags=["voice"], route_class=limited_body_route(JSON_BODY_LIMIT_BYTES)
    )
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
        """What the configured provider offers, and what it can do with it."""
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
        """Set or clear the chosen voice, refusing one the provider does not offer."""
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
    """Register the preview route, for a provider that declares preview."""

    @router.get(
        "/voices/{voice_id}/preview",
        summary="Hear a voice",
        response_class=Response,
        dependencies=SIGNED_IN,
        responses={200: {"content": {"audio/mpeg": {}}}},
    )
    async def preview(voice_id: str, voices: Voices) -> Response:
        try:
            sample = await voices.preview(voice_id)
        except DomainError as error:
            # No such voice and no sample are one answer.
            raise ApiError(
                status.HTTP_404_NOT_FOUND,
                "no_sample",
                "There is nothing to play for that voice.",
            ) from error

        return Response(
            content=sample.audio,
            media_type=sample.media_type,
            # Cached privately: a sample never changes for its voice.
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
