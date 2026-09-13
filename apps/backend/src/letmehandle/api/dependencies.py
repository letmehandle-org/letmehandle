"""Wiring.

The one place that knows which implementation each port gets. Everything above depends on the
interfaces, so swapping a provider is an edit here and nowhere else — which is the property the
whole architecture exists to have.

It is also the one place allowed to name a provider. A test asserts that no module under
`domain/` does.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# Imported at run time, not under TYPE_CHECKING: FastAPI resolves these annotations while
# building the dependency graph, and a forward reference it cannot resolve is an error at the
# first request rather than at import.
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
from letmehandle.api.errors import ApiError
from letmehandle.application.auth.deletion import AccountDeletion
from letmehandle.application.auth.service import AuthenticationPolicy, AuthenticationService
from letmehandle.application.calls.history import CallHistoryService
from letmehandle.application.calls.reports import CallReporting
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


# How many requests one signed-in user may make in a window. The app makes a handful per screen and
# a few more per call; this is far beyond that, and what it stops is one account, or one stolen
# token, driving the database as fast as a loop can. Counted per process, as the limiter says.
SIGNED_IN_REQUESTS_PER_WINDOW: Final = 300
SIGNED_IN_WINDOW: Final = timedelta(minutes=1)

# auto_error=False so that a missing header produces this module's own 401 rather than
# FastAPI's, which would have a different body from every other error the API returns.
_bearer = HTTPBearer(auto_error=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request, committed at the end if nothing raised.

    Reached only through `RequestSession`, which ends it before the response is sent.
    """
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


# The session as routes receive it. Ended when the route function returns, before its response is
# sent, rather than after as a dependency's teardown otherwise is: a client answered before the
# commit could read back nothing of what it was told was written, and a commit that failed after
# the answer would be a success nobody learned was undone.
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
        policy=AuthenticationPolicy(
            refresh_token_lifetime=container.refresh_token_lifetime,
        ),
    )


def get_account_deletion(
    request: Request,
    session: RequestSession,
) -> AccountDeletion:
    """Deleting an account, on this request's session, ending its calls through the orchestrator.

    The orchestrator is the one owner of every live call, and there is none in a deployment that
    carries no calls.
    """
    return AccountDeletion(
        users=SqlUserRepository(session, container_of(request).clock),
        challenges=SqlOTPChallengeRepository(session),
        calls=request.app.state.orchestrator,
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

    container = container_of(request)
    try:
        authenticated = container.signer.verify(credentials.credentials)
    except DomainError as error:
        raise unauthorised from error

    # After the token is proved, so the count belongs to an account rather than to whatever an
    # anonymous caller writes in a header, and before anything touches the database.
    decision = await container.rate_limiter.check(
        f"signed-in:{authenticated.user_id.value}",
        limit=SIGNED_IN_REQUESTS_PER_WINDOW,
        window=SIGNED_IN_WINDOW,
    )
    if not decision.allowed:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many requests. Try again shortly.",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    return authenticated


async def get_current_user(
    authenticated: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
    session: RequestSession,
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
    session: RequestSession,
) -> PreferencesService:
    """Preferences and onboarding, on this request's session."""
    clock = container_of(request).clock
    return PreferencesService(
        preferences=SqlPreferencesRepository(session, clock),
        onboarding=SqlOnboardingRepository(session, clock),
    )


def get_call_history_service(
    request: Request,
    session: RequestSession,
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
    """The voices this deployment offers.

    Held on the container rather than built per request: a catalogue does not change between
    requests, and the routes that exist were decided from its capabilities at startup.
    """
    return container_of(request).voices


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
    """Stored escalation contexts, on this request's session, or a 503 as call history gives.

    What the user was told is sealed like the call it was about, so without the keys there is
    nothing here to read either.
    """
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
CallReports = Annotated[CallReporting, Depends(get_call_reporting)]
CallHistory = Annotated[CallHistoryService, Depends(get_call_history_service)]
Devices = Annotated[DeviceRegistrationService, Depends(get_device_service)]
EscalationContexts = Annotated[EscalationContextRepository, Depends(get_escalation_contexts)]
