"""The OAuth access token FCM's HTTP v1 API is called with.

Obtained the way a service account obtains one without a vendor SDK: a JWT naming the account,
the messaging scope and the token endpoint, signed RS256 with the account's key and exchanged at
that endpoint for a bearer token that lives about an hour. It is cached and replaced five minutes
before it expires, and one refresh runs at a time however many deliveries are waiting on it.

The key, the assertion and the access token are credentials: none is logged or put in an error.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from letmehandle.adapters.notification.shared import CredentialError, load_private_key

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.ports.clock import Clock

MESSAGING_SCOPE: Final = "https://www.googleapis.com/auth/firebase.messaging"
# S105 reads "token" in the name as a password; it is the address of the endpoint.
DEFAULT_TOKEN_URI: Final = "https://oauth2.googleapis.com/token"  # noqa: S105
GRANT_TYPE: Final = "urn:ietf:params:oauth:grant-type:jwt-bearer"
ASSERTION_LIFETIME: Final = timedelta(hours=1)
REFRESH_MARGIN: Final = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    """The parts of a service account document that obtaining a token needs."""

    client_email: str
    private_key_id: str
    private_key: RSAPrivateKey
    token_uri: str

    @classmethod
    def parse(cls, document: str) -> ServiceAccount:
        """Read a service account's JSON. Failures name the field, never the content."""
        try:
            values = json.loads(document)
        except ValueError:
            raise CredentialError("the FCM service account is not valid JSON") from None
        if not isinstance(values, dict):
            raise CredentialError("the FCM service account is not a JSON object")
        missing = [
            name
            for name in ("client_email", "private_key_id", "private_key")
            if not isinstance(values.get(name), str) or not values[name].strip()
        ]
        if missing:
            raise CredentialError(f"the FCM service account has no {', '.join(missing)}")
        key = load_private_key(values["private_key"], what="the FCM service account key")
        if not isinstance(key, RSAPrivateKey):
            raise CredentialError("the FCM service account key must be an RSA key")
        token_uri = values.get("token_uri")
        return cls(
            client_email=values["client_email"],
            private_key_id=values["private_key_id"],
            private_key=key,
            token_uri=token_uri if isinstance(token_uri, str) and token_uri else DEFAULT_TOKEN_URI,
        )


class AccessTokenError(Exception):
    """No access token could be obtained. `retryable` says whether trying later could help."""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class AccessTokenSource:
    """Obtains, caches and refreshes the access token."""

    def __init__(self, account: ServiceAccount, *, client: httpx.AsyncClient, clock: Clock) -> None:
        self._account = account
        self._client = client
        self._clock = clock
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._refresh_at: datetime | None = None

    async def current(self) -> str:
        """A token that will outlive the request it is used for."""
        async with self._lock:
            if (
                self._token is None
                or self._refresh_at is None
                or self._clock.now() >= self._refresh_at
            ):
                self._token, self._refresh_at = await self._obtain()
            return self._token

    def rejected(self) -> None:
        """FCM refused the token. The next request obtains a new one."""
        self._token = None
        self._refresh_at = None

    async def _obtain(self) -> tuple[str, datetime]:
        now = self._clock.now()
        assertion = jwt.encode(
            {
                "iss": self._account.client_email,
                "scope": MESSAGING_SCOPE,
                "aud": self._account.token_uri,
                "iat": int(now.timestamp()),
                "exp": int((now + ASSERTION_LIFETIME).timestamp()),
            },
            self._account.private_key,
            algorithm="RS256",
            headers={"kid": self._account.private_key_id},
        )
        try:
            response = await self._client.post(
                self._account.token_uri, data={"grant_type": GRANT_TYPE, "assertion": assertion}
            )
        except httpx.HTTPError as error:
            raise AccessTokenError(f"transport: {type(error).__name__}", retryable=True) from None

        if response.status_code != 200:
            # A 4xx is the account or its key being refused, which will not fix itself; the error
            # code Google returns (`invalid_grant`, say) is kept and its description is not.
            raise AccessTokenError(
                f"token endpoint {response.status_code} {_oauth_error(response)}".rstrip(),
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        try:
            body = response.json()
            token = body["access_token"]
            lifetime = timedelta(seconds=int(body.get("expires_in", 3600)))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AccessTokenError(
                "token endpoint answered without a token", retryable=True
            ) from None
        if not isinstance(token, str) or not token:
            raise AccessTokenError("token endpoint answered without a token", retryable=True)
        # Refreshed ahead of expiry, or halfway through a lifetime too short for the margin.
        margin = REFRESH_MARGIN if lifetime > 2 * REFRESH_MARGIN else lifetime / 2
        return token, now + lifetime - margin


def _oauth_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and error.replace("_", "").isalpha() else ""
