"""What a user is told about forwarding calls, only where a deployment's calls arrive forwarded."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.domain.models.forwarding import ForwardingNumbers
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.region import IN, US
from tests.integration.conftest import Api, bearer, running, sign_in

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

FORWARD_TO = "+12025550100"
# Each region's forwarding number; the Indian ones are too short to reach anybody.
US_LINE = "+12025550100"
IN_LINE = "+91555010"
IN_USER = "+91555001"
UK_USER = "+447700900123"


@pytest.fixture
async def forwarded(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """The application as a deployment whose calls arrive forwarded to `FORWARD_TO`."""
    forwarding = ForwardingNumbers(elsewhere=PhoneNumber.parse(FORWARD_TO))
    async with running(database_url, schema, forwarding=forwarding) as ready:
        yield ready


@pytest.fixture
async def by_region(session: object, database_url: str, schema: str) -> AsyncIterator[Api]:
    """A deployment with a line in the US and one in India, and none for anywhere else."""
    forwarding = ForwardingNumbers(
        by_region={US: PhoneNumber.parse(US_LINE), IN: PhoneNumber.parse(IN_LINE)}
    )
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


class TestLinesByRegion:
    @pytest.mark.parametrize(("number", "line"), [("+12025550143", US_LINE), (IN_USER, IN_LINE)])
    async def test_each_user_is_told_the_number_in_their_own_region(
        self, by_region: Api, number: str, line: str
    ) -> None:
        tokens = await sign_in(by_region, number)
        profile = await by_region.client.get("/v1/me", headers=bearer(tokens))
        progress = await by_region.client.get("/v1/onboarding", headers=bearer(tokens))

        assert profile.json()["call_forwarding"] == {"number": line}
        assert "call_forwarding" in progress.json()["remaining"]

    async def test_a_user_no_line_serves_is_told_no_number_and_is_not_asked_to_forward(
        self, by_region: Api
    ) -> None:
        # No line serves their region, so setup names no number to forward to.
        tokens = await sign_in(by_region, UK_USER)
        profile = await by_region.client.get("/v1/me", headers=bearer(tokens))
        recorded = await by_region.client.post(
            "/v1/onboarding", headers=bearer(tokens), json={"step": "call_forwarding"}
        )

        assert profile.json()["call_forwarding"] is None
        assert recorded.status_code == 422
        assert recorded.json()["error"] == "step_not_asked"
