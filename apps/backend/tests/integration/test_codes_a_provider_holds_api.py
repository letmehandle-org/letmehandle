"""Signing in over HTTP where the provider makes and checks the code: each check outcome (D-042)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from letmehandle.adapters.database.session import unit_of_work
from letmehandle.domain.errors import ProviderError
from tests.contracts.fakes import CheckingOTPProvider
from tests.integration.conftest import NUMBER, bearer, running

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration


@pytest.fixture
def provider() -> CheckingOTPProvider:
    return CheckingOTPProvider()


@pytest.fixture
async def holding(
    session: object, database_url: str, schema: str, provider: CheckingOTPProvider
) -> AsyncIterator[Api]:
    async with running(database_url, schema, otp=provider) as ready:
        yield ready


async def challenge(api: Api) -> str:
    response = await api.client.post("/v1/auth/challenge", json={"phone_number": NUMBER})
    assert response.status_code == 202, response.text
    challenge_id: str = response.json()["challenge_id"]
    return challenge_id


async def attempts(api: Api, challenge_id: str) -> tuple[int, str | None]:
    async with unit_of_work(api.app.state.session_factory) as session:
        row = (
            await session.execute(
                text("SELECT attempts, code_hash FROM otp_challenges WHERE id = :id"),
                {"id": challenge_id},
            )
        ).one()
    return row.attempts, row.code_hash


async def test_the_code_the_provider_sent_signs_in_and_nothing_about_it_is_stored(
    holding: Api, provider: CheckingOTPProvider
) -> None:
    challenge_id = await challenge(holding)
    verified = await holding.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": provider.code}
    )
    assert verified.status_code == 200

    me = await holding.client.get("/v1/me", headers=bearer(verified.json()))
    assert me.json()["phone_number"] == NUMBER
    assert provider.sent == []
    assert await attempts(holding, challenge_id) == (1, None)


async def test_a_wrong_code_is_the_same_refusal_and_counts(holding: Api) -> None:
    challenge_id = await challenge(holding)
    refused = await holding.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": "000000"}
    )
    assert refused.status_code == 401
    assert refused.json()["error"] == "invalid_credentials"
    assert await attempts(holding, challenge_id) == (1, None)


async def test_a_provider_that_cannot_check_is_a_temporary_failure_that_counts_nothing(
    holding: Api, provider: CheckingOTPProvider
) -> None:
    challenge_id = await challenge(holding)
    provider.failure = ProviderError(provider.name, "the API refused with HTTP 503", retryable=True)
    unchecked = await holding.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": provider.code}
    )
    assert unchecked.status_code == 503
    assert unchecked.json()["error"] == "provider_unavailable"
    assert int(unchecked.headers["Retry-After"]) > 0
    assert "access_token" not in unchecked.json()
    assert await attempts(holding, challenge_id) == (0, None)

    again = await holding.client.post(
        "/v1/auth/verify", json={"challenge_id": challenge_id, "code": provider.code}
    )
    assert again.status_code == 200
