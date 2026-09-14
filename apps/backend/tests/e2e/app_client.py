"""The mobile app as the backend sees it: HTTP requests over the real routes with a bearer token."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.models.phone_number import PhoneNumber

if TYPE_CHECKING:
    from fastapi import FastAPI

type Json = dict[str, Any]


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
    """Every posture explicit and no hours, so the assistant always answers (D-030)."""
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
        me = await self.http.get("/v1/me", headers=headers)
        assert me.status_code == 200, me.text
        profile: Json = me.json()
        return Account(UserId(profile["id"]), PhoneNumber.parse(profile["phone_number"]), headers)

    async def configure(self, account: Account, preferences: Json) -> Json:
        """Change the sections given and leave the rest, as the settings screens do."""
        response = await self.http.patch(
            "/v1/preferences", json=preferences, headers=account.headers
        )
        assert response.status_code == 200, response.text
        stored = await self.http.get("/v1/preferences", headers=account.headers)
        assert stored.json() == response.json(), stored.text
        body: Json = stored.json()
        return body

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

    async def _read(self, account: Account, path: str) -> Json | None:
        response = await self.http.get(path, headers=account.headers)
        if response.status_code == 404:
            return None
        assert response.status_code == 200, response.text
        body: Json = response.json()
        return body
