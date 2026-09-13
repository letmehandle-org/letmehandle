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


class TestOnboarding:
    async def test_forwarding_is_asked_right_after_call_handling(self, forwarded: Api) -> None:
        tokens = await sign_in(forwarded)
        response = await forwarded.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_handling"}
        )

        assert response.json() == {
            "completed": ["call_handling"],
            "skipped": [],
            "remaining": ["call_forwarding", "hours", "authority", "notifications"],
            "next_step": "call_forwarding",
            "is_complete": False,
        }

    async def test_forwarding_cannot_be_skipped(self, forwarded: Api) -> None:
        # Skipped, no call ever reaches the assistant, and a finished setup would say it works.
        tokens = await sign_in(forwarded)
        response = await forwarded.client.post(
            "/v1/onboarding",
            headers=bearer(tokens),
            json={"step": "call_forwarding", "skipped": True},
        )

        assert response.status_code == 422
        assert response.json()["error"] == "invalid_request"

    async def test_setup_is_not_complete_until_forwarding_is_answered(self, forwarded: Api) -> None:
        tokens = await sign_in(forwarded)
        for step in ["call_handling", "hours", "authority", "notifications"]:
            response = await forwarded.client.post(
                "/v1/onboarding", headers=bearer(tokens), json={"step": step}
            )
        assert response.json()["next_step"] == "call_forwarding"

        response = await forwarded.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_forwarding"}
        )
        assert response.json()["is_complete"] is True

    async def test_where_nothing_is_forwarded_the_step_is_not_listed(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.get("/v1/onboarding", headers=bearer(tokens))

        assert response.json()["remaining"] == [
            "call_handling",
            "hours",
            "authority",
            "notifications",
        ]

    async def test_where_nothing_is_forwarded_recording_the_step_is_refused(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_forwarding"}
        )

        assert response.status_code == 422
        assert response.json()["error"] == "step_not_asked"
        progress = await api.client.get("/v1/onboarding", headers=bearer(tokens))
        assert progress.json()["completed"] == []
