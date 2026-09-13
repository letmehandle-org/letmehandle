"""The mobile app, as far as the backend can tell: HTTP requests with a bearer token.

Everything a scenario sets up or reads back about a user goes through here, over the real routes, so
a scenario proves what the app would see rather than what storage holds. Signing in reads the code
the mock one-time-password provider recorded, which is reading the text message.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI

type Json = dict[str, Any]

# How long a write's read-back may take to show it. A commit over loopback takes milliseconds.
READ_BACK_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class Account:
    """A signed-in user: who they are, and how their requests say so."""

    user_id: UserId
    number: PhoneNumber
    headers: dict[str, str]


def call_handling(
    *,
    default: str = "handle_with_agent",
    anonymous: str = "handle_with_agent",
    escalate_at_or_above: int = 40,
) -> Json:
    """Call handling stated in full, with no hours: the assistant answers around the clock (D-030).

    Every posture is explicit, so a scenario does not depend on which routing function reads it or
    on a default that later changes.
    """
    return {
        "call_handling": {
            "default_posture": default,
            "anonymous_posture": anonymous,
            "posture_by_category": {},
            "blocked_categories": [],
            "escalate_at_or_above": escalate_at_or_above,
        },
        "hours": {"active": None},
    }


class AppClient:
    """Requests to one running application, as the app on a user's phone makes them."""

    def __init__(self, app: FastAPI, base_url: str) -> None:
        # Public for a scenario that has to make a request the way no helper here would.
        self._app = app
        self.http = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def aclose(self) -> None:
        await self.http.aclose()

    async def sign_in(self, number: str) -> Account:
        challenge = await self.http.post("/v1/auth/challenge", json={"phone_number": number})
        assert challenge.status_code == 202, challenge.text
        otp = self._app.state.container.otp
        assert isinstance(otp, MockOTPProvider)
        verified = await self.http.post(
            "/v1/auth/verify",
            json={"challenge_id": challenge.json()["challenge_id"], "code": otp.sent[-1][1]},
        )
        assert verified.status_code == 200, verified.text
        headers = {"Authorization": f"Bearer {verified.json()['access_token']}"}
        profile = await self._committed(lambda: self.http.get("/v1/me", headers=headers))
        return Account(UserId(profile["id"]), PhoneNumber.parse(profile["phone_number"]), headers)

    async def configure(self, account: Account, preferences: Json) -> Json:
        """Change the sections given and leave the rest, as the settings screens do."""
        response = await self.http.patch(
            "/v1/preferences", json=preferences, headers=account.headers
        )
        assert response.status_code == 200, response.text
        body: Json = response.json()
        stored = await self._committed(
            lambda: self.http.get("/v1/preferences", headers=account.headers),
            until=lambda read: read == body,
        )
        return stored

    async def register_device(self, account: Account, platform: str, token: str) -> None:
        response = await self.http.put(
            "/v1/devices", json={"platform": platform, "token": token}, headers=account.headers
        )
        assert response.status_code == 204, response.text

    async def call(self, account: Account, call_id: str) -> Json | None:
        """The call as its detail screen shows it, or None while there is no such call."""
        return await self._read(account, f"/v1/calls/{call_id}")

    async def calls(self, account: Account) -> list[Json]:
        response = await self.http.get("/v1/calls", headers=account.headers)
        assert response.status_code == 200, response.text
        listed: list[Json] = response.json()["calls"]
        return listed

    async def escalation(self, account: Account, call_id: str) -> Json | None:
        """What the user was told about an escalation, or None when there was none."""
        return await self._read(account, f"/v1/escalations/{call_id}")

    async def report(self, account: Account, *reports: Json) -> Json:
        """A handset reporting what happened to its calls."""
        response = await self.http.post(
            "/v1/calls/reports", json={"reports": list(reports)}, headers=account.headers
        )
        assert response.status_code == 200, response.text
        receipt: Json = response.json()
        return receipt

    async def _committed(
        self,
        read: Callable[[], Awaitable[httpx.Response]],
        *,
        until: Callable[[Json], bool] = lambda _: True,
    ) -> Json:
        """Read back what a write just answered for, until the read shows it.

        A write's response is sent before its transaction commits (see the scenario on
        acknowledged writes), so a request made the moment one returns can find it missing.
        Waiting here keeps that defect from being every scenario's flake; it has its own.
        """
        async with asyncio.timeout(READ_BACK_SECONDS):
            while True:
                response = await read()
                if response.status_code == 200 and until(response.json()):
                    body: Json = response.json()
                    return body
                await asyncio.sleep(0.01)

    async def _read(self, account: Account, path: str) -> Json | None:
        response = await self.http.get(path, headers=account.headers)
        if response.status_code == 404:
            return None
        assert response.status_code == 200, response.text
        body: Json = response.json()
        return body
