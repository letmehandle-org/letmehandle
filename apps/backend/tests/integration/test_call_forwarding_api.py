"""What a user is told about forwarding calls, where a deployment needs it and where not.

A streaming deployment hears about a call only when the user's carrier forwards it, so the app has
to be able to say where to. A deployment that needs nothing forwarded must not say anything, or a
user sets up forwarding to a number that answers nobody.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.models.forwarding import CallForwarding
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.integration.conftest import Api, bearer, running, sign_in

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FORWARD_TO = "+12025550100"


@pytest.fixture
async def forwarded(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """The application as a deployment whose calls arrive forwarded to `FORWARD_TO`."""
    forwarding = CallForwarding(PhoneNumber.parse(FORWARD_TO))
    async with running(database_url, schema, forwarding=forwarding) as ready:
        yield ready


class TestProfile:
    async def test_it_names_the_number_to_forward_to(self, forwarded: Api) -> None:
        tokens = await sign_in(forwarded)
        response = await forwarded.client.get("/v1/me", headers=bearer(tokens))

        assert response.status_code == 200
        assert response.json()["call_forwarding"] == {"number": FORWARD_TO}

    async def test_a_profile_update_says_the_same(self, forwarded: Api) -> None:
        tokens = await sign_in(forwarded)
        response = await forwarded.client.patch(
            "/v1/me", headers=bearer(tokens), json={"display_name": "Sam"}
        )

        assert response.json()["call_forwarding"] == {"number": FORWARD_TO}

    async def test_nothing_is_named_where_nothing_needs_forwarding(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.get("/v1/me", headers=bearer(tokens))

        assert response.json()["call_forwarding"] is None
