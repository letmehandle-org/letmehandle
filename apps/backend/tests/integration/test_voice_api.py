"""Choosing a voice over HTTP, against a real database.

Two things are being proved here. The first is ordinary: a choice is stored, comes back, and
belongs to one user. The second is the one this phase exists for — the routes the application
has depend on what the configured provider can do, so a deployment whose provider cannot play a
sample has no route that would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

from letmehandle.adapters.voice.builtin import (
    SHIPPED_DEFAULT_VOICE_ID,
    SHIPPED_VOICES,
    BuiltInVoiceProvider,
)
from letmehandle.domain.ports.voice import VoiceSample
from tests.integration.conftest import ANOTHER_NUMBER, bearer, running, sign_in

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

ANOTHER_VOICE = SHIPPED_VOICES[1].id
SAMPLE = VoiceSample(audio=b"a short recording", media_type="audio/mpeg")


@pytest.fixture
async def previewing(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """An application whose provider has sample audio, and therefore declares preview."""
    provider = BuiltInVoiceProvider(
        SHIPPED_VOICES,
        default_voice_id=SHIPPED_DEFAULT_VOICE_ID,
        samples={SHIPPED_DEFAULT_VOICE_ID: SAMPLE},
    )
    async with running(database_url, schema, voices=provider) as ready:
        yield ready


async def selection(api: Api, tokens: dict[str, Any]) -> dict[str, Any]:
    response = await api.client.get("/v1/preferences/voice", headers=bearer(tokens))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestCatalogue:
    async def test_it_describes_the_provider_rather_than_being_bundled(self, api: Api) -> None:
        # The client draws this screen from what the server says it has. A bundled list is a
        # list that shows a voice the deployment cannot speak with.
        tokens = await sign_in(api)
        body = (await api.client.get("/v1/voices", headers=bearer(tokens))).json()

        assert [voice["id"] for voice in body["voices"]] == [v.id for v in SHIPPED_VOICES]
        assert body["default_voice_id"] == SHIPPED_DEFAULT_VOICE_ID

    async def test_it_declares_what_the_provider_cannot_do(self, api: Api) -> None:
        # D-009: the interface renders from these, and a true it did not mean puts a training
        # flow in front of somebody it will fail for.
        tokens = await sign_in(api)
        capabilities = (await api.client.get("/v1/voices", headers=bearer(tokens))).json()[
            "capabilities"
        ]

        assert capabilities["builtin_voices"] is True
        assert capabilities["cloning"] is False
        assert capabilities["custom_voice"] is False
        assert capabilities["preview"] is False


class TestChoosing:
    async def test_a_new_user_has_chosen_nothing_and_still_has_a_voice(self, api: Api) -> None:
        tokens = await sign_in(api)
        body = await selection(api, tokens)

        assert body["persona_voice_id"] is None
        # The end of the fallback chain. Nothing chosen must never mean a call with no voice.
        assert body["resolved_voice_id"] == SHIPPED_DEFAULT_VOICE_ID

    async def test_a_chosen_voice_is_stored_and_used(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.put(
            "/v1/preferences/voice",
            headers=bearer(tokens),
            json={"persona_voice_id": ANOTHER_VOICE},
        )

        assert response.status_code == 200, response.text
        assert response.json()["resolved_voice_id"] == ANOTHER_VOICE
        assert (await selection(api, tokens))["persona_voice_id"] == ANOTHER_VOICE

    async def test_clearing_the_choice_returns_to_the_default(self, api: Api) -> None:
        tokens = await sign_in(api)
        await api.client.put(
            "/v1/preferences/voice",
            headers=bearer(tokens),
            json={"persona_voice_id": ANOTHER_VOICE},
        )

        cleared = await api.client.put(
            "/v1/preferences/voice", headers=bearer(tokens), json={"persona_voice_id": None}
        )

        assert cleared.json()["persona_voice_id"] is None
        assert cleared.json()["resolved_voice_id"] == SHIPPED_DEFAULT_VOICE_ID

    async def test_a_voice_the_provider_does_not_offer_is_refused_rather_than_stored(
        self, api: Api
    ) -> None:
        # Storing it would fall through to the default on every call, which looks to the user
        # exactly like their choice being ignored.
        tokens = await sign_in(api)
        response = await api.client.put(
            "/v1/preferences/voice", headers=bearer(tokens), json={"persona_voice_id": "ghost"}
        )

        assert response.status_code == 422
        assert (await selection(api, tokens))["persona_voice_id"] is None

    async def test_choosing_a_voice_leaves_every_other_preference_alone(self, api: Api) -> None:
        # The same rule the rest of preferences lives by, arriving through a different route.
        tokens = await sign_in(api)
        await api.client.patch(
            "/v1/preferences",
            headers=bearer(tokens),
            json={"personality": {"formality": "warm", "verbosity": "brief", "topics": ["bins"]}},
        )

        await api.client.put(
            "/v1/preferences/voice",
            headers=bearer(tokens),
            json={"persona_voice_id": ANOTHER_VOICE},
        )

        preferences = (await api.client.get("/v1/preferences", headers=bearer(tokens))).json()
        assert preferences["personality"]["formality"] == "warm"
        assert preferences["personality"]["topics"] == ["bins"]

    async def test_a_choice_survives_signing_out_and_back_in(self, api: Api) -> None:
        # The property somebody actually experiences: the phone is reinstalled, or the token
        # expired, and the assistant still sounds the way they left it. Held on the server
        # rather than on the device, which is what makes that true.
        first = await sign_in(api)
        await api.client.put(
            "/v1/preferences/voice",
            headers=bearer(first),
            json={"persona_voice_id": ANOTHER_VOICE},
        )

        again = await sign_in(api)

        assert (await selection(api, again))["persona_voice_id"] == ANOTHER_VOICE

    async def test_replacing_every_other_preference_does_not_change_the_voice(
        self, api: Api
    ) -> None:
        # PUT /v1/preferences resets what it does not mention, deliberately — but it has no
        # field for the voice, so without an exception it could only ever destroy it.
        tokens = await sign_in(api)
        await api.client.put(
            "/v1/preferences/voice",
            headers=bearer(tokens),
            json={"persona_voice_id": ANOTHER_VOICE},
        )

        replaced = await api.client.put(
            "/v1/preferences", headers=bearer(tokens), json={"locale": "en-GB"}
        )
        assert replaced.status_code == 200, replaced.text

        assert (await selection(api, tokens))["persona_voice_id"] == ANOTHER_VOICE

    async def test_a_choice_belongs_to_one_user(self, api: Api) -> None:
        mine = await sign_in(api)
        await api.client.put(
            "/v1/preferences/voice", headers=bearer(mine), json={"persona_voice_id": ANOTHER_VOICE}
        )

        theirs = await sign_in(api, ANOTHER_NUMBER)
        assert (await selection(api, theirs))["persona_voice_id"] is None

    async def test_a_token_for_a_deleted_account_is_refused_everywhere(self, api: Api) -> None:
        # A token outlives the account it names. Checking only the signature would leave a
        # deleted account reading the catalogue for the rest of that token's life, which is a
        # weaker answer than the one every other route in this API gives.
        tokens = await sign_in(api)
        factory = api.app.state.session_factory
        async with factory() as session:
            await session.execute(text("DELETE FROM refresh_tokens"))
            await session.execute(text("DELETE FROM users"))
            await session.commit()

        assert (await api.client.get("/v1/voices", headers=bearer(tokens))).status_code == 401
        assert (
            await api.client.get("/v1/preferences/voice", headers=bearer(tokens))
        ).status_code == 401

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.get("/v1/voices")).status_code == 401
        assert (await api.client.get("/v1/preferences/voice")).status_code == 401
        assert (await api.client.put("/v1/preferences/voice", json={})).status_code == 401


class TestPreviewFollowsTheProvider:
    async def test_a_provider_that_cannot_preview_has_no_preview_route(self, api: Api) -> None:
        # Not a route that refuses: a route that exists appears in the schema, generates a
        # client method, and gets a button drawn for it somewhere.
        tokens = await sign_in(api)
        response = await api.client.get(
            f"/v1/voices/{SHIPPED_DEFAULT_VOICE_ID}/preview", headers=bearer(tokens)
        )
        assert response.status_code == 404

    async def test_it_is_absent_from_the_schema_too(self, api: Api) -> None:
        schema = (await api.client.get("/openapi.json")).json()
        assert not [path for path in schema["paths"] if path.endswith("/preview")]

    async def test_a_provider_that_can_preview_serves_the_audio(self, previewing: Api) -> None:
        tokens = await sign_in(previewing)
        response = await previewing.client.get(
            f"/v1/voices/{SHIPPED_DEFAULT_VOICE_ID}/preview", headers=bearer(tokens)
        )

        assert response.status_code == 200
        assert response.content == SAMPLE.audio
        assert response.headers["content-type"] == SAMPLE.media_type

    async def test_it_appears_in_the_schema_when_it_exists(self, previewing: Api) -> None:
        schema = (await previewing.client.get("/openapi.json")).json()
        assert "/v1/voices/{voice_id}/preview" in schema["paths"]

    async def test_a_voice_with_no_sample_is_not_found_rather_than_a_failure(
        self, previewing: Api
    ) -> None:
        # A real, selectable voice whose sample is missing. The provider distinguishes it from
        # an unknown identifier; over HTTP both are honestly "there is nothing to play".
        tokens = await sign_in(previewing)
        response = await previewing.client.get(
            f"/v1/voices/{ANOTHER_VOICE}/preview", headers=bearer(tokens)
        )
        assert response.status_code == 404

    async def test_an_unknown_voice_is_not_found(self, previewing: Api) -> None:
        tokens = await sign_in(previewing)
        response = await previewing.client.get("/v1/voices/ghost/preview", headers=bearer(tokens))
        assert response.status_code == 404

    async def test_a_preview_still_needs_a_token(self, previewing: Api) -> None:
        response = await previewing.client.get(f"/v1/voices/{SHIPPED_DEFAULT_VOICE_ID}/preview")
        assert response.status_code == 401

    async def test_the_catalogue_says_preview_is_available(self, previewing: Api) -> None:
        tokens = await sign_in(previewing)
        body = (await previewing.client.get("/v1/voices", headers=bearer(tokens))).json()
        assert body["capabilities"]["preview"] is True
