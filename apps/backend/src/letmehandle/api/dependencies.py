"""Wiring: which implementation each port gets, per request."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Imported at run time: FastAPI resolves these annotations when it builds the dependency graph.
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlEscalationContextRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.repositories import (
    SqlCallReportRepository,
    SqlDeviceRepository,
    SqlOnboardingRepository,
    SqlOTPChallengeRepository,
    SqlPreferencesRepository,
    SqlRefreshTokenRepository,
    SqlUserRepository,
)
from letmehandle.api.errors import ApiError, database_unavailable, rate_limited
from letmehandle.application.auth.deletion import AccountDeletion
from letmehandle.application.auth.service import AuthenticationService
from letmehandle.application.calls.history import CallHistoryService
from letmehandle.application.calls.reports import CallReporting
from letmehandle.application.escalation.devices import DeviceRegistrationService
from letmehandle.application.preferences.service import PreferencesService
from letmehandle.domain.errors import DomainError
from letmehandle.domain.models.auth import AuthenticatedUser
from letmehandle.domain.models.forwarding import ForwardingNumbers
from letmehandle.domain.models.onboarding import OnboardingFlow
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.repositories import EscalationContextRepository, UserRepository
from letmehandle.domain.ports.voice import VoiceProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.bootstrap import Container


def container_of(request: Request) -> Container:
    """The application's chosen implementations, typed."""
    container: Container = request.app.state.container
    return container


# How many requests one signed-in account may make per window, counted per process.
SIGNED_IN_REQUESTS_PER_WINDOW: Final = 300
SIGNED_IN_WINDOW: Final = timedelta(minutes=1)

# A missing header is this module's 401, in the API's own error shape.
_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, committed unless an unexpected error rolls it back."""
    factory: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    if factory is None:
        raise database_unavailable()
    async with factory() as session:
        try:
            yield session
        except ApiError:
            # A deliberate refusal keeps its side effects, such as a revoked token family.
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


# The request's session, ended when the route returns and before its response is sent.
type RequestSession = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_authentication_service(
    request: Request,
    session: RequestSession,
) -> AuthenticationService:
    """The sign-in use case, with this request's session."""
    container = container_of(request)
    return AuthenticationService(
        users=SqlUserRepository(session, container.clock),
        challenges=SqlOTPChallengeRepository(session),
        refresh_tokens=SqlRefreshTokenRepository(session),
        otp=container.otp,
        code_hasher=container.code_hasher,
        token_hasher=container.token_hasher,
        secrets=container.secrets,
        signer=container.signer,
        clock=container.clock,
        ids=container.ids,
        rate_limiter=container.rate_limiter,
        policy=replace(
            container.auth_limits, refresh_token_lifetime=container.refresh_token_lifetime
        ),
        metrics=container.metrics,
    )


def get_account_deletion(
    request: Request,
    session: RequestSession,
) -> AccountDeletion:
    """Account deletion on this request's session, ending live calls through the orchestrator."""
    return AccountDeletion(
        users=SqlUserRepository(session, container_of(request).clock),
        challenges=SqlOTPChallengeRepository(session),
        calls=request.app.state.orchestrator,
    )


def _not_authenticated() -> ApiError:
    return ApiError(
        status.HTTP_401_UNAUTHORIZED,
        "not_authenticated",
        "This request needs a valid access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_authenticated_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    """Who this request's access token names, or a 401; rate limited per account."""
    if credentials is None or not credentials.credentials:
        raise _not_authenticated()

    container = container_of(request)
    try:
        authenticated = container.signer.verify(credentials.credentials)
    except DomainError as error:
        raise _not_authenticated() from error

    decision = await container.rate_limiter.check(
        f"signed-in:{authenticated.user_id.value}",
        limit=SIGNED_IN_REQUESTS_PER_WINDOW,
        window=SIGNED_IN_WINDOW,
    )
    if not decision.allowed:
        raise rate_limited(decision.retry_after_seconds, "Too many requests. Try again shortly.")
    return authenticated


async def get_current_user(
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    session: RequestSession,
    request: Request,
) -> User:
    """The user this request is from; a token naming no account is a 401."""
    user = await SqlUserRepository(session, container_of(request).clock).get(authenticated.user_id)
    if user is None:
        raise _not_authenticated()
    return user


def get_preferences_service(
    request: Request,
    session: RequestSession,
    user: Annotated[User, Depends(get_current_user)],
) -> PreferencesService:
    """Preferences and onboarding, asking about forwarding only where this user has a number."""
    container = container_of(request)
    clock = container.clock
    forwarded = container.forwarding.for_user(user.phone_number) is not None
    return PreferencesService(
        preferences=SqlPreferencesRepository(session, clock),
        onboarding=SqlOnboardingRepository(session, clock),
        onboarding_flow=OnboardingFlow(calls_are_forwarded=forwarded),
    )


def get_call_history_service(
    request: Request,
    session: RequestSession,
) -> CallHistoryService:
    """A user's calls, on this request's session, or a 503 without transcript keys (D-014)."""
    container = container_of(request)
    cipher = container.transcript_cipher
    if cipher is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "call_history_unavailable",
            "This service cannot read call history: it has no transcript keys configured.",
        )
    return CallHistoryService(
        calls=SqlCallRepository(session, cipher, container.clock),
        summaries=SqlSummaryRepository(session, cipher, container.clock),
        transcripts=SqlTranscriptRepository(session, cipher),
        preferences=SqlPreferencesRepository(session, container.clock),
    )


def get_call_reporting(
    request: Request,
    session: RequestSession,
) -> CallReporting:
    """Accepting a handset's reports about its calls, on this request's session."""
    container = container_of(request)
    return CallReporting(
        reports=SqlCallReportRepository(session, container.clock),
        sink=container.reported_calls,
        rate_limiter=container.rate_limiter,
    )


def get_voice_provider(request: Request) -> VoiceProvider:
    """The voices this deployment offers."""
    return container_of(request).voices


def get_call_forwarding(request: Request) -> ForwardingNumbers:
    """The numbers this deployment's users forward their calls to, by region."""
    return container_of(request).forwarding


def get_user_repository(
    request: Request,
    session: RequestSession,
) -> UserRepository:
    """Storage for accounts, on this request's session."""
    return SqlUserRepository(session, container_of(request).clock)


def get_device_service(
    request: Request,
    session: RequestSession,
) -> DeviceRegistrationService:
    """The device token lifecycle, on this request's session."""
    return DeviceRegistrationService(SqlDeviceRepository(session, container_of(request).clock))


def get_escalation_contexts(
    request: Request,
    session: RequestSession,
) -> EscalationContextRepository:
    """Stored escalation contexts, on this request's session, or a 503 without transcript keys."""
    cipher = container_of(request).transcript_cipher
    if cipher is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "escalations_unavailable",
            "This service cannot read escalations: it has no transcript keys configured.",
        )
    return SqlEscalationContextRepository(session, cipher)


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthService = Annotated[AuthenticationService, Depends(get_authentication_service)]
Deletion = Annotated[AccountDeletion, Depends(get_account_deletion)]
Users = Annotated[UserRepository, Depends(get_user_repository)]
Preferences = Annotated[PreferencesService, Depends(get_preferences_service)]
Voices = Annotated[VoiceProvider, Depends(get_voice_provider)]
Forwarding = Annotated[ForwardingNumbers, Depends(get_call_forwarding)]
CallReports = Annotated[CallReporting, Depends(get_call_reporting)]
CallHistory = Annotated[CallHistoryService, Depends(get_call_history_service)]
Devices = Annotated[DeviceRegistrationService, Depends(get_device_service)]
EscalationContexts = Annotated[EscalationContextRepository, Depends(get_escalation_contexts)]
