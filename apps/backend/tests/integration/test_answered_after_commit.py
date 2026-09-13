"""A request's writes are committed before it is answered.

A client told a write succeeded reads it straight back, and a commit that fails must be a failed
request rather than a success nobody hears was undone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.conftest import NUMBER

if TYPE_CHECKING:
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration


class RefusingSession(AsyncSession):
    """A session whose every commit is refused, as a database lost mid-request refuses it."""

    async def commit(self) -> None:
        raise ConnectionError("the connection was reset during the commit")


async def test_a_write_whose_commit_fails_is_not_answered_as_a_success(api: Api) -> None:
    engine = api.app.state.engine
    api.app.state.session_factory = async_sessionmaker(
        engine, class_=RefusingSession, expire_on_commit=False, autoflush=False
    )
    # The application's failure reaches the client as the response it sent, not as an exception.
    transport = ASGITransport(app=api.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/v1/auth/challenge", json={"phone_number": NUMBER})

    assert response.status_code >= 500
