"""Signing in, over HTTP."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Request, Response, status

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
from letmehandle.api.client_address import client_source
from letmehandle.api.dependencies import (
    AuthService,
    CurrentUser,
    Deletion,
    Devices,
    Forwarding,
    Preferences,
    Users,
    container_of,
)
from letmehandle.api.errors import (
    UNPROCESSABLE,
    ApiError,
    invalid_request,
    provider_unavailable,
    rate_limited,
)
from letmehandle.api.schemas import (
    CallForwardingResponse,
    ChallengeRequest,
    ChallengeResponse,
    ProfileResponse,
    RefreshRequest,
    SignOutRequest,
    TokenResponse,
    UpdateProfileRequest,
    VerifyRequest,
)
from letmehandle.application.auth.service import (
    AuthenticationError,
    CodeMayHaveBeenSentError,
    CodeNotCheckedError,
    RateLimitedError,
    UnservedNumberError,
)
from letmehandle.application.preferences.service import PreferenceChanges
from letmehandle.domain.errors import InvariantError, UnreachableNumberError
from letmehandle.domain.models.auth import TokenPair
from letmehandle.domain.models.forwarding import CallForwarding
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.notification import DeviceToken
from letmehandle.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["authentication"],
    route_class=limited_body_route(JSON_BODY_LIMIT_BYTES),
)


def _source_of(request: Request) -> str | None:
    """The key attempts from this request's origin are counted under."""
    return client_source(request, container_of(request).trusted_proxies)


def _rate_limited(error: RateLimitedError) -> ApiError:
    return rate_limited(error.retry_after_seconds, "Too many attempts. Try again shortly.")


def _not_valid(message: str) -> ApiError:
    """One response for every sign-in failure: expired, wrong, used and unknown alike."""
    return ApiError(status.HTTP_401_UNAUTHORIZED, "invalid_credentials", message)


def _tokens(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in_seconds=pair.expires_in_seconds,
    )


def _profile(user: User, locale: str, forwarding: CallForwarding | None) -> ProfileResponse:
    return ProfileResponse(
        id=user.id.value,
        phone_number=user.phone_number.value,
        display_name=user.display_name,
        locale=locale,
        call_forwarding=(
            None if forwarding is None else CallForwardingResponse(number=forwarding.number.value)
        ),
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
    """Send a code to a number, answering alike whether or not it has an account."""
    try:
        issued = await service.request_challenge(
            PhoneNumber(body.phone_number), source=_source_of(request)
        )
    except RateLimitedError as error:
        raise _rate_limited(error) from error
    except UnservedNumberError as error:
        raise ApiError(
            UNPROCESSABLE,
            "unserved_country",
            "Sign-in codes are not sent to numbers in this country.",
        ) from error
    except CodeMayHaveBeenSentError as error:
        # Committed, so the code counts against the number (D-037).
        raise provider_unavailable(error.retry_after_seconds) from error
    except UnreachableNumberError as error:
        # Other provider failures reach the application's handler, which rolls the challenge back.
        raise ApiError(
            UNPROCESSABLE,
            "number_unreachable",
            "That number cannot receive a sign-in code. Check it and try again.",
        ) from error

    return ChallengeResponse(
        challenge_id=issued.challenge_id,
        expires_in_seconds=issued.expires_in_seconds,
        resend_after_seconds=issued.resend_after_seconds,
    )


@router.post("/auth/verify", response_model=TokenResponse, summary="Exchange a code")
async def verify(body: VerifyRequest, request: Request, service: AuthService) -> TokenResponse:
    """Exchange a correct code for a session, creating the account if there is not one."""
    try:
        pair = await service.verify(body.challenge_id, body.code, source=_source_of(request))
    except RateLimitedError as error:
        raise _rate_limited(error) from error
    except AuthenticationError as error:
        raise _not_valid("That code is not valid.") from error
    except CodeNotCheckedError as error:
        # No attempt is counted, so the same code may be offered again after the wait (D-042).
        logger.warning("provider_failed", provider=error.provider, reason=error.reason)
        raise provider_unavailable(error.retry_after_seconds) from error

    return _tokens(pair)


@router.post("/auth/refresh", response_model=TokenResponse, summary="Renew a session")
async def refresh(body: RefreshRequest, service: AuthService) -> TokenResponse:
    """Exchange a refresh token for a new pair, retiring the old one (D-036)."""
    try:
        pair = await service.refresh(body.refresh_token)
    except AuthenticationError as error:
        raise _not_valid("That session is not valid.") from error

    return _tokens(pair)


@router.post("/auth/signout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
async def sign_out(body: SignOutRequest, service: AuthService, devices: Devices) -> Response:
    """End this refresh token's session and forget the device on its account; always succeeds."""
    owner = await service.sign_out(body.refresh_token)
    if owner is not None and body.device is not None:
        await devices.remove(owner, DeviceToken(body.device.platform, body.device.token))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=ProfileResponse, summary="The signed-in user")
async def read_me(
    user: CurrentUser, preferences: Preferences, forwarding: Forwarding
) -> ProfileResponse:
    locale = (await preferences.get(user.id)).locale
    return _profile(user, locale, forwarding.for_user(user.phone_number))


@router.patch("/me", response_model=ProfileResponse, summary="Update the profile")
async def update_me(
    body: UpdateProfileRequest,
    user: CurrentUser,
    users: Users,
    preferences: Preferences,
    forwarding: Forwarding,
) -> ProfileResponse:
    """Change the fields sent and leave absent ones alone (D-023)."""
    try:
        stored = await preferences.apply(user.id, PreferenceChanges(locale=body.locale))
    except InvariantError as error:
        raise invalid_request(error) from error
    updated = user
    if body.display_name is not None:
        updated = replace(updated, display_name=body.display_name)
        await users.update(updated)

    return _profile(updated, stored.locale, forwarding.for_user(updated.phone_number))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="Delete the account")
async def delete_me(user: CurrentUser, deletion: Deletion) -> Response:
    """Delete the signed-in account and everything held for it, ending any call in progress."""
    await deletion.delete(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
