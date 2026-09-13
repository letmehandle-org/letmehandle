"""Issuing and verifying access tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import jwt

from letmehandle.adapters.security.hashing import MIN_KEY_LENGTH
from letmehandle.domain.errors import DomainError, InvariantError
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.models.auth import AuthenticatedUser
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.ports.security import TokenSigner

if TYPE_CHECKING:
    from letmehandle.domain.ports.clock import Clock

_ALGORITHM: Final = "HS256"
_ISSUER: Final = "letmehandle"
# The audience is verified as well as the signature.
_AUDIENCE: Final = "letmehandle-api"


class InvalidTokenError(DomainError):
    """The token was not issued here, has been tampered with, or has expired."""

    failure_kind = FailureKind.NOT_PERMITTED


class JWTTokenSigner(TokenSigner):
    """Signed, stateless, short-lived access tokens; revocation belongs to refresh tokens."""

    def __init__(self, *, signing_key: str, lifetime: timedelta, clock: Clock) -> None:
        if len(signing_key) < MIN_KEY_LENGTH:
            raise InvariantError(
                f"the signing key must be at least {MIN_KEY_LENGTH} characters; everything else "
                "about authentication rests on it"
            )
        if lifetime <= timedelta(0):
            raise InvariantError("an access token that has already expired is not useful")
        self._key = signing_key
        self._lifetime = lifetime
        self._clock = clock

    def issue(self, user_id: UserId, issued_at: datetime) -> tuple[str, datetime]:
        expires_at = issued_at + self._lifetime
        claims: dict[str, Any] = {
            "sub": user_id.value,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": _ISSUER,
            "aud": _AUDIENCE,
        }
        return jwt.encode(claims, self._key, algorithm=_ALGORITHM), expires_at

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            claims = jwt.decode(
                token,
                self._key,
                # Exactly one algorithm, which prevents algorithm confusion.
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    # Expiry is checked below against the injected clock, not the machine's.
                    "verify_exp": False,
                },
            )
        except jwt.PyJWTError as error:
            # The reason is not passed on, so a caller cannot tell expired from forged.
            raise InvalidTokenError("that token is not valid") from error

        authenticated = AuthenticatedUser(
            user_id=UserId(str(claims["sub"])),
            issued_at=datetime.fromtimestamp(float(claims["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
        )
        if not authenticated.is_valid_at(self._clock.now()):
            raise InvalidTokenError("that token is not valid")
        return authenticated
