"""Signing in, over HTTP."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Request, Response, status

from letmehandle.api.dependencies import AuthService, CurrentUser, Users
from letmehandle.api.errors import ApiError
from letmehandle.api.schemas import (
    ChallengeRequest,
    ChallengeResponse,
    ProfileResponse,
    RefreshRequest,
    SignOutRequest,
    TokenResponse,
    UpdateProfileRequest,
    VerifyRequest,
)
from letmehandle.application.auth.service import AuthenticationError, RateLimitedError
from letmehandle.domain.models.auth import TokenPair
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User

router = APIRouter(prefix="/v1", tags=["authentication"])


def _source_of(request: Request) -> str | None:
    """Something to count attempts against, per origin.

    The immediate peer, not a forwarded header. A header is set by whoever is calling, so a
    limit keyed on one is a limit the attacker chooses the key for. Behind a proxy this needs
    the proxy's own trusted forwarding, which is deployment configuration rather than
    something to guess at here.
    """
    return request.client.host if request.client else None


def _not_valid(message: str) -> ApiError:
    """One shape for every sign-in failure.

    Expired, wrong, already used and never existed are the same response. The differences are
    exactly what an attacker needs to narrow down what they are holding.
    """
    return ApiError(status.HTTP_401_UNAUTHORIZED, "invalid_credentials", message)


def _tokens(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in_seconds=pair.expires_in_seconds,
    )


def _profile(user: User) -> ProfileResponse:
    return ProfileResponse(
        id=user.id.value,
        phone_number=user.phone_number.value,
        display_name=user.display_name,
        locale=user.preferences.locale,
    )


@router.post(
    "/auth/challenge",
    response_model=ChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a sign-in code",
)
async def request_challenge(
    body: ChallengeRequest, request: Request, service: AuthService
) -> ChallengeResponse:
    """Send a code to a number.

    Answers the same way whether or not the number has an account. Telling them apart is the
    expensive half of attacking a phone-number identity, and this is where it would be given
    away for nothing.
    """
    try:
        issued = await service.request_challenge(
            PhoneNumber(body.phone_number), source=_source_of(request)
        )
    except RateLimitedError as error:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many attempts. Try again shortly.",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error

    return ChallengeResponse(
        challenge_id=issued.challenge_id, expires_in_seconds=issued.expires_in_seconds
    )


@router.post("/auth/verify", response_model=TokenResponse, summary="Exchange a code")
async def verify(body: VerifyRequest, service: AuthService) -> TokenResponse:
    """Exchange a correct code for a session, creating the account if there is not one."""
    try:
        pair = await service.verify(body.challenge_id, body.code)
    except AuthenticationError as error:
        raise _not_valid("That code is not valid.") from error

    return _tokens(pair)


@router.post("/auth/refresh", response_model=TokenResponse, summary="Renew a session")
async def refresh(body: RefreshRequest, service: AuthService) -> TokenResponse:
    """Exchange a refresh token for a new pair.

    The old one stops working immediately. Presenting it again means a copy exists somewhere
    it should not, and every session from that sign-in ends.
    """
    try:
        pair = await service.refresh(body.refresh_token)
    except AuthenticationError as error:
        raise _not_valid("That session is not valid.") from error

    return _tokens(pair)


@router.post("/auth/signout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def sign_out(body: SignOutRequest, service: AuthService) -> Response:
    """End the session this refresh token belongs to.

    Always succeeds. Somebody signing out has nothing to gain from being told their token was
    already invalid, and saying so would tell an attacker whether a token they hold is real.
    """
    await service.sign_out(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=ProfileResponse, summary="The signed-in user")
async def read_me(user: CurrentUser) -> ProfileResponse:
    return _profile(user)


@router.patch("/me", response_model=ProfileResponse, summary="Update the profile")
async def update_me(body: UpdateProfileRequest, user: CurrentUser, users: Users) -> ProfileResponse:
    """Change what the assistant knows about the person it represents.

    A field that is absent is left alone rather than cleared. A client sending only what it
    changed is the ordinary case, and reading absence as "set to nothing" would quietly erase
    everything it did not mention.
    """
    updated = user
    if body.display_name is not None:
        updated = replace(updated, display_name=body.display_name)
    if body.locale is not None:
        updated = replace(updated, preferences=replace(updated.preferences, locale=body.locale))

    if updated != user:
        await users.update(updated)

    return _profile(updated)
