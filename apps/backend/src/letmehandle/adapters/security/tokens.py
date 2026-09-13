"""Issuing and verifying access tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import jwt

from letmehandle.domain.errors import DomainError, InvariantError
from letmehandle.domain.failures import FailureKind
from letmehandle.domain.models.auth import AuthenticatedUser
from letmehandle.domain.models.identifiers import UserId
from letmehandle.domain.ports.security import TokenSigner

if TYPE_CHECKING:
    from letmehandle.domain.ports.clock import Clock

_ALGORITHM: Final = "HS256"
_ISSUER: Final = "letmehandle"
# The audience is checked as well as the signature. Without it, a token minted for one purpose
# by the same key is accepted for another.
_AUDIENCE: Final = "letmehandle-api"


class InvalidTokenError(DomainError):
    """The token was not issued here, has been tampered with, or has expired."""

    failure_kind = FailureKind.NOT_PERMITTED


class JWTTokenSigner(TokenSigner):
    """Signed, stateless access tokens.

    Stateless on purpose: verification is a signature check rather than a database lookup,
    which is what lets every request carry one without costing a query. The price is that an
    access token cannot be revoked, which is why it is short-lived and why revocation is the
    refresh token's job.
    """

    def __init__(self, *, signing_key: str, lifetime: timedelta, clock: Clock) -> None:
        if len(signing_key) < 32:
            raise InvariantError(
                "the signing key must be at least 32 characters; everything else about "
                "authentication rests on it"
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
                # A list of exactly one. Accepting whatever the token's header asks for is the
                # algorithm-confusion attack, and it is a one-word mistake to make.
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={
                    "require": ["exp", "iat", "sub", "iss", "aud"],
                    # Expiry is checked below, against the clock this application was given.
                    # The library would otherwise check it against the machine's own, which
                    # means one part of the system disagrees with every other part about what
                    # time it is — and makes the behaviour untestable without waiting.
                    "verify_exp": False,
                },
            )
        except jwt.PyJWTError as error:
            # The reason is deliberately not passed on: expired, forged and malformed are all
            # "not valid" to a caller, and the difference is information an attacker can use.
            raise InvalidTokenError("that token is not valid") from error

        authenticated = AuthenticatedUser(
            user_id=UserId(str(claims["sub"])),
            issued_at=datetime.fromtimestamp(float(claims["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
        )
        if not authenticated.is_valid_at(self._clock.now()):
            raise InvalidTokenError("that token is not valid")
        return authenticated
