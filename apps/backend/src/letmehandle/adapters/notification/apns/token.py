"""The provider token APNs authenticates every request with.

A JWT signed with the team's `.p8` key, ES256 only. Apple's rules, which this enforces:
refresh no more often than once every 20 minutes (more often is `TooManyProviderTokenUpdates`)
and no less often than once every 60 (older is `ExpiredProviderToken`). So one token is made and
reused until it is 50 minutes old, and a rejected one is replaced early only if it is at least 20
minutes old.

The token is a credential. It is never logged and never part of an error message.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

import jwt
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePrivateKey

from letmehandle.adapters.notification.shared import CredentialError, load_private_key

if TYPE_CHECKING:
    from datetime import datetime

    from letmehandle.domain.ports.clock import Clock

REFRESH_AFTER: Final = timedelta(minutes=50)
MINIMUM_AGE_BEFORE_REPLACING: Final = timedelta(minutes=20)


class APNsProviderToken:
    """Makes, caches and refreshes the provider token."""

    def __init__(self, *, key_id: str, team_id: str, private_key: str, clock: Clock) -> None:
        if not key_id.strip() or not team_id.strip():
            raise CredentialError("an APNs key needs both its key id and its team id")
        key = load_private_key(private_key, what="the APNs signing key")
        if not isinstance(key, EllipticCurvePrivateKey) or not isinstance(key.curve, SECP256R1):
            # APNs verifies ES256 and nothing else, so any other key would sign tokens that every
            # request then fails with.
            raise CredentialError("the APNs signing key must be a P-256 elliptic-curve key")
        self._key = key
        self._key_id = key_id
        self._team_id = team_id
        self._clock = clock
        self._token: str | None = None
        self._issued_at: datetime | None = None

    def current(self) -> str:
        """A token young enough to be accepted, made now if the cached one is too old."""
        now = self._clock.now()
        if self._token is None or self._issued_at is None or now - self._issued_at >= REFRESH_AFTER:
            self._token = jwt.encode(
                {"iss": self._team_id, "iat": int(now.timestamp())},
                self._key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            self._issued_at = now
        return self._token

    def rejected(self) -> None:
        """APNs called the token stale. Replace it, unless that would be replacing too often."""
        if self._issued_at is not None and (
            self._clock.now() - self._issued_at >= MINIMUM_AGE_BEFORE_REPLACING
        ):
            self._token = None
            self._issued_at = None
