"""Wiring.

The one place that knows which implementation each port gets. Everything above depends on the
interfaces, so swapping a provider is an edit here and nowhere else — which is the property the
whole architecture exists to have.

It is also the one place allowed to name a provider. A test asserts that no module under
`domain/` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Imported at run time, not under TYPE_CHECKING: FastAPI resolves these annotations while
# building the dependency graph, and a forward reference it cannot resolve is an error at the
# first request rather than at import.
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.repositories import (
    SqlDeviceRepository,
    SqlEscalationContextRepository,
    SqlOnboardingRepository,
    SqlOTPChallengeRepository,
    SqlPreferencesRepository,
    SqlRefreshTokenRepository,
    SqlUserRepository,
)
from letmehandle.api.errors import ApiError
from letmehandle.application.auth.service import AuthenticationPolicy, AuthenticationService
from letmehandle.application.calls.history import CallHistoryService
from letmehandle.application.escalation.devices import DeviceRegistrationService
from letmehandle.application.preferences.service import PreferencesService
from letmehandle.domain.errors import DomainError
from letmehandle.domain.models.auth import AuthenticatedUser
from letmehandle.domain.models.user import User
from letmehandle.domain.ports.repositories import EscalationContextRepository, UserRepository
from letmehandle.domain.ports.voice import VoiceProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.bootstrap import Container


def container_of(request: Request) -> Container:
    """The application's chosen implementations.

    Read through one typed accessor rather than reaching into application state everywhere:
    `app.state` is untyped, and untyped access spreads until nothing can be checked.
    """
    container: Container = request.app.state.container
    return container


# auto_error=False so that a missing header produces this module's own 401 rather than
# FastAPI's, which would have a different body from every other error the API returns.
_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, committed at the end if nothing raised."""
    factory: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    if factory is None:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            "This service is not connected to its database.",
        )
    async with factory() as session:
        try:
            yield session
        except ApiError:
            # Committed, not rolled back, and this is the important case. A deliberate refusal
            # is a completed request whose side effects are part of the decision: detecting a
            # replayed refresh token revokes the whole family and *then* refuses, and a
            # rollback here would undo the revocation and leave the stolen token working.
            #
            # Anything else — an unexpected failure — rolls back, because a half-written
            # sign-in is worse than a failed one.
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


def get_authentication_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
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
        policy=AuthenticationPolicy(
            refresh_token_lifetime=container.refresh_token_lifetime,
        ),
    )


async def get_authenticated_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthenticatedUser:
    """Who this request is from, or a 401.

    A dependency rather than middleware. Middleware decides from a path pattern, and a path
    pattern is a rule somebody forgets to update when they add a route; a dependency is
    declared by the route itself and cannot be forgotten without the route not compiling.
    """
    unauthorised = ApiError(
        status.HTTP_401_UNAUTHORIZED,
        "not_authenticated",
        "This request needs a valid access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials:
        raise unauthorised

    try:
        return container_of(request).signer.verify(credentials.credentials)
    except DomainError as error:
        raise unauthorised from error


async def get_current_user(
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> User:
    """The user this request is from.

    A token can outlive the account it names — deleted, or issued before a restore. Treating
    that as unauthenticated rather than as a missing row keeps the response the same as every
    other failure to authenticate.
    """
    user = await SqlUserRepository(session, container_of(request).clock).get(authenticated.user_id)
    if user is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "not_authenticated",
            "This request needs a valid access token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_preferences_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PreferencesService:
    """Preferences and onboarding, on this request's session."""
    clock = container_of(request).clock
    return PreferencesService(
        preferences=SqlPreferencesRepository(session, clock),
        onboarding=SqlOnboardingRepository(session, clock),
    )


def get_call_history_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CallHistoryService:
    """A user's calls, on this request's session, or a 503 when nothing here can open them.

    Every call record is sealed, down to who called, so without the keys there is no history to
    serve — only rows nobody can read. Saying so beats a 500 on every call a user opens.
    """
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


def get_voice_provider(request: Request) -> VoiceProvider:
    """The voices this deployment offers.

    Held on the container rather than built per request: a catalogue does not change between
    requests, and the routes that exist were decided from its capabilities at startup.
    """
    return container_of(request).voices


def get_user_repository(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRepository:
    """Storage for accounts, on this request's session."""
    return SqlUserRepository(session, container_of(request).clock)


def get_device_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceRegistrationService:
    """The device token lifecycle, on this request's session."""
    return DeviceRegistrationService(SqlDeviceRepository(session, container_of(request).clock))


def get_escalation_contexts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> EscalationContextRepository:
    """Stored escalation contexts, on this request's session."""
    return SqlEscalationContextRepository(session)


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthService = Annotated[AuthenticationService, Depends(get_authentication_service)]
Users = Annotated[UserRepository, Depends(get_user_repository)]
Preferences = Annotated[PreferencesService, Depends(get_preferences_service)]
Voices = Annotated[VoiceProvider, Depends(get_voice_provider)]
CallHistory = Annotated[CallHistoryService, Depends(get_call_history_service)]
Devices = Annotated[DeviceRegistrationService, Depends(get_device_service)]
EscalationContexts = Annotated[EscalationContextRepository, Depends(get_escalation_contexts)]
