"""The composition root: the one module that chooses, and names, each port's implementation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, Final, assert_never

from fastapi import APIRouter

from letmehandle import __version__
from letmehandle.adapters.agent.strands.agent import StrandsCallAgent
from letmehandle.adapters.agent.strands.model import openai_compatible_model
from letmehandle.adapters.agent.strands.summary import StrandsSummaryDrafter
from letmehandle.adapters.clock import SystemClock, UUIDGenerator
from letmehandle.adapters.closing import close_each
from letmehandle.adapters.database.call_repositories import (
    SqlCallRepository,
    SqlEscalationContextRepository,
    SqlSummaryRepository,
    SqlTranscriptRepository,
)
from letmehandle.adapters.database.repositories import (
    SqlDeviceRepository,
    SqlPreferencesRepository,
    SqlUserRepository,
)
from letmehandle.adapters.database.session import unit_of_work
from letmehandle.adapters.database.timeline import SqlCallTimelineRepository
from letmehandle.adapters.notification.apns.provider import (
    APNsEnvironment,
    APNsNotificationProvider,
)
from letmehandle.adapters.notification.apns.token import APNsProviderToken
from letmehandle.adapters.notification.fcm import provider as fcm
from letmehandle.adapters.notification.fcm.credentials import AccessTokenSource, ServiceAccount
from letmehandle.adapters.notification.shared import CredentialError
from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from letmehandle.adapters.otp.twilio_verify import VerifyOTPProvider
from letmehandle.adapters.rate_limit.in_memory import InMemoryRateLimiter
from letmehandle.adapters.security.hashing import (
    DeterministicHasher,
    ScryptHasher,
    SystemSecretGenerator,
)
from letmehandle.adapters.security.tokens import JWTTokenSigner
from letmehandle.adapters.security.transcript_cipher import AesGcmTranscriptCipher
from letmehandle.adapters.speech.elevenlabs.protocol import (
    DEFAULT_WIRE_FORMAT as ELEVENLABS_WIRE_FORMAT,
)
from letmehandle.adapters.speech.elevenlabs.provider import ElevenLabsSpeechProvider
from letmehandle.adapters.speech.elevenlabs.websocket import (
    websocket_opener as elevenlabs_opener,
)
from letmehandle.adapters.speech.gpt_live.provider import GptLiveSpeechProvider
from letmehandle.adapters.speech.gpt_live.websocket import websocket_opener as gpt_live_opener
from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT as REALTIME_WIRE_FORMAT
from letmehandle.adapters.speech.realtime.provider import RealtimeSpeechProvider
from letmehandle.adapters.speech.realtime.websocket import websocket_opener as realtime_opener
from letmehandle.adapters.tracing.opentelemetry import otlp_tracer
from letmehandle.adapters.transport.android_native.transport import AndroidNativeCallTransport
from letmehandle.adapters.transport.twilio.ownership import ForwardedCallOwnership
from letmehandle.adapters.transport.twilio.rest import HttpTelephonyApi
from letmehandle.adapters.transport.twilio.routes import build_router as build_twilio_router
from letmehandle.adapters.transport.twilio.signature import SignatureVerifier
from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport, TwilioConfig
from letmehandle.adapters.voice.builtin import BuiltInVoiceProvider
from letmehandle.application.agent.conclusion import JudgementConclusion
from letmehandle.application.agent.escalation import EscalationService
from letmehandle.application.agent.tools.registry import tools_for_judgements
from letmehandle.application.auth.service import AuthenticationPolicy
from letmehandle.application.calls.reports import ReportedCallOwnership
from letmehandle.application.calls.summariser import ModelCallSummariser
from letmehandle.application.escalation.dispatch import EscalationDispatcher, EscalationStores
from letmehandle.application.orchestration.orchestrator import CallOrchestrator
from letmehandle.application.orchestration.ports import (
    AssistantServices,
    Bounds,
    CallJudging,
    CallLine,
    CallOwnership,
    CallStores,
)
from letmehandle.application.resilience.circuit import Circuits
from letmehandle.config.settings import (
    APNsEnvironmentName,
    ConfigurationError,
    OTPProviderName,
    Settings,
    SpeechProviderName,
    TelephonyProviderName,
)
from letmehandle.config.telephony_lines import LineProviderName
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from letmehandle.domain.models.forwarding import ForwardingNumbers
from letmehandle.observability.in_process import InProcessMetrics, MetricsFanOut
from letmehandle.observability.metrics import LoggingMetricsRecorder
from letmehandle.observability.tracing import NoTracer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from ipaddress import IPv4Network, IPv6Network

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from strands.models.model import Model

    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener
    from letmehandle.application.agent.ports import CallActions
    from letmehandle.application.calls.summariser import CallSummariser
    from letmehandle.config.telephony_lines import TelephonyLine
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.models.region import TelephonyRegion
    from letmehandle.domain.ports.call_transport import CallTransport
    from letmehandle.domain.ports.clock import Clock, IdGenerator
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.notification import NotificationProvider
    from letmehandle.domain.ports.otp import OTPProvider
    from letmehandle.domain.ports.rate_limit import RateLimiter
    from letmehandle.domain.ports.reported_calls import CallEventSink
    from letmehandle.domain.ports.security import (
        SecretGenerator,
        SecretHasher,
        TokenSigner,
        TranscriptCipher,
    )
    from letmehandle.domain.ports.speech import SpeechProvider
    from letmehandle.domain.ports.tracing import Tracer
    from letmehandle.domain.ports.voice import VoiceProvider


@dataclass(frozen=True, slots=True)
class Container:
    """Everything chosen at startup; codes hash salted, refresh tokens keyed to be looked up."""

    clock: Clock
    ids: IdGenerator
    secrets: SecretGenerator
    code_hasher: SecretHasher
    token_hasher: SecretHasher
    signer: TokenSigner
    otp: OTPProvider
    voices: VoiceProvider
    rate_limiter: RateLimiter
    refresh_token_lifetime: timedelta
    # None when no transcript keys are configured.
    transcript_cipher: TranscriptCipher | None
    # The handset transport, which turns handsets' reports into call events.
    reported_calls: CallEventSink
    # The numbers users forward their unanswered and busy calls to, by region.
    forwarding: ForwardingNumbers
    # One per configured platform, possibly none: escalation works without push (D-016).
    notifications: tuple[NotificationProvider, ...] = ()
    auth_limits: AuthenticationPolicy = field(default_factory=AuthenticationPolicy)
    # Proxies whose forwarding headers are believed.
    trusted_proxies: tuple[IPv4Network | IPv6Network, ...] = ()
    metrics: MetricsRecorder | None = None


def build_container(
    settings: Settings,
    *,
    voices: VoiceProvider,
    reported_calls: CallEventSink,
    metrics: MetricsRecorder | None = None,
) -> Container:
    """The implementations for this configuration, around the voices and handset feed given."""
    clock = SystemClock()
    signing_key = settings.require_signing_key()
    refresh_token_lifetime = timedelta(seconds=settings.auth_refresh_token_ttl_seconds)

    return Container(
        clock=clock,
        ids=UUIDGenerator(),
        secrets=SystemSecretGenerator(),
        code_hasher=ScryptHasher(),
        token_hasher=DeterministicHasher(signing_key),
        signer=JWTTokenSigner(
            signing_key=signing_key,
            lifetime=timedelta(seconds=settings.auth_access_token_ttl_seconds),
            clock=clock,
        ),
        otp=_build_otp_provider(settings),
        voices=voices,
        rate_limiter=InMemoryRateLimiter(clock),
        refresh_token_lifetime=refresh_token_lifetime,
        transcript_cipher=(
            None
            if settings.transcript_encryption_keys is None
            else AesGcmTranscriptCipher(settings.require_transcript_keys())
        ),
        notifications=build_notification_providers(settings, clock=clock),
        reported_calls=reported_calls,
        forwarding=build_call_forwarding(settings),
        auth_limits=AuthenticationPolicy(
            refresh_token_lifetime=refresh_token_lifetime,
            allowed_calling_codes=settings.otp_allowed_calling_codes,
            challenges_per_hour=settings.otp_challenges_per_hour,
            challenges_per_hour_per_calling_code=settings.otp_challenges_per_hour_per_calling_code,
        ),
        trusted_proxies=settings.trusted_proxy_cidrs,
        metrics=metrics or LoggingMetricsRecorder(),
    )


def build_call_forwarding(settings: Settings) -> ForwardingNumbers:
    """Each region's line's first number, and the every-region line's for everybody else."""
    by_region: dict[TelephonyRegion, PhoneNumber] = {}
    elsewhere: PhoneNumber | None = None
    for line in settings.require_telephony_lines():
        if line.regions is None:
            elsewhere = line.numbers[0]
        else:
            by_region.update(dict.fromkeys(line.regions, line.numbers[0]))
    return ForwardingNumbers(by_region=by_region, elsewhere=elsewhere)


def build_notification_providers(
    settings: Settings, *, clock: Clock
) -> tuple[NotificationProvider, ...]:
    """A provider for each platform with any variable set, credentials parsed and complete."""
    providers: list[NotificationProvider] = []
    if settings.apns_configured:
        providers.append(_apns_provider(settings, clock=clock))
    if settings.fcm_configured:
        providers.append(_fcm_provider(settings, clock=clock))
    return tuple(providers)


def _apns_provider(settings: Settings, *, clock: Clock) -> NotificationProvider:
    apns = settings.require_apns()
    try:
        token = APNsProviderToken(
            key_id=apns.key_id, team_id=apns.team_id, private_key=apns.private_key, clock=clock
        )
    except CredentialError as error:
        variables = "APNS_PRIVATE_KEY, APNS_KEY_ID or APNS_TEAM_ID"
        raise ConfigurationError(f"{variables}: {error}") from None
    return APNsNotificationProvider(
        token=token,
        topic=apns.topic,
        environment=_APNS_ENVIRONMENTS[apns.environment],
        clock=clock,
    )


def _fcm_provider(settings: Settings, *, clock: Clock) -> NotificationProvider:
    credentials = settings.require_fcm()
    try:
        account = ServiceAccount.parse(credentials.service_account_json)
    except CredentialError as error:
        raise ConfigurationError(f"FCM_SERVICE_ACCOUNT_JSON: {error}") from None
    client = fcm.build_client(timeout=fcm.DEFAULT_REQUEST_TIMEOUT)
    return fcm.FCMNotificationProvider(
        project_id=credentials.project_id,
        tokens=AccessTokenSource(account, client=client, clock=clock),
        client=client,
    )


_APNS_ENVIRONMENTS: Final = {
    APNsEnvironmentName.SANDBOX: APNsEnvironment.SANDBOX,
    APNsEnvironmentName.PRODUCTION: APNsEnvironment.PRODUCTION,
}


@dataclass(frozen=True, slots=True)
class Observability:
    """Where metrics and spans go, the in-process metrics diagnostics reads, circuits, and close."""

    metrics: MetricsRecorder
    in_process: InProcessMetrics
    tracer: Tracer
    circuits: Circuits
    close: Callable[[], None]


def build_observability(settings: Settings) -> Observability:
    """Metrics to the log and to diagnostics, and spans to a collector when one is configured."""
    in_process = InProcessMetrics()
    metrics = MetricsFanOut(LoggingMetricsRecorder(), in_process)
    endpoint = settings.tracing_otlp_endpoint
    if endpoint is None:
        tracer: Tracer = NoTracer()
        close: Callable[[], None] = _nothing_to_flush
    else:
        exporting = otlp_tracer(str(endpoint), version=__version__)
        tracer, close = exporting.tracer, exporting.shutdown
    return Observability(
        metrics=metrics,
        in_process=in_process,
        tracer=tracer,
        circuits=Circuits(metrics=metrics),
        close=close,
    )


def _nothing_to_flush() -> None:
    return None


def build_escalation_dispatcher(
    container: Container,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    observability: Observability,
) -> EscalationDispatcher:
    """The escalation dispatcher, storing sealed through short units of work."""
    clock = container.clock
    cipher = _sealing(container, "escalate: what the user is told about a call is sealed")

    @asynccontextmanager
    async def stores() -> AsyncIterator[EscalationStores]:
        async with unit_of_work(session_factory) as session:
            yield EscalationStores(
                devices=SqlDeviceRepository(session, clock),
                contexts=SqlEscalationContextRepository(session, cipher),
            )

    return EscalationDispatcher(
        providers=container.notifications,
        stores=stores,
        metrics=observability.metrics,
        tracer=observability.tracer,
        circuits=observability.circuits,
    )


def _sealing(container: Container, needed_to: str) -> TranscriptCipher:
    """The transcript cipher, or a failure naming the keys and what they are needed for."""
    cipher = container.transcript_cipher
    if cipher is None:
        raise ConfigurationError(f"TRANSCRIPT_ENCRYPTION_KEYS is required to {needed_to}.")
    return cipher


async def close_providers(container: Container) -> None:
    """Close each provider's connection, once, as the application stops."""
    await close_each((container.otp, *container.notifications))


def build_voice_provider(settings: Settings) -> VoiceProvider:
    """The voices this deployment offers: the configured catalogue its speech service speaks."""
    voices, default_voice = settings.require_voice_catalogue()
    return BuiltInVoiceProvider(voices, default_voice_id=default_voice)


# What the speech adapters can convert from: a microphone's wideband audio, and a phone line's.
_SPEECH_INPUT_FORMATS: Final = (SPEECH_WIDEBAND, TELEPHONY_NARROWBAND)


def build_speech_provider(
    settings: Settings,
    *,
    metrics: MetricsRecorder,
    wrap_connection: Callable[[ConnectionOpener], ConnectionOpener] | None = None,
) -> SpeechProvider:
    """The speech service by its protocol; `wrap_connection` stands between session and network."""
    wrap = wrap_connection or _unwrapped
    key = settings.speech_api_key
    api_key = None if key is None else key.get_secret_value()
    match settings.speech_provider:
        case SpeechProviderName.REALTIME:
            endpoint, model = settings.require_speech_model()
            return RealtimeSpeechProvider(
                wrap(realtime_opener(endpoint, model=model, api_key=api_key)),
                metrics,
                languages=settings.speech_languages,
                input_formats=_SPEECH_INPUT_FORMATS,
                # The protocol's own wire format, so nothing is converted twice on the way out.
                output_format=REALTIME_WIRE_FORMAT,
                transcription_model=settings.speech_transcription_model,
            )
        case SpeechProviderName.ELEVENLABS:
            endpoint, agent_id = settings.require_speech_agent()
            return ElevenLabsSpeechProvider(
                wrap(elevenlabs_opener(endpoint, agent_id=agent_id, api_key=api_key)),
                metrics,
                languages=settings.speech_languages,
                input_formats=_SPEECH_INPUT_FORMATS,
                # What an agent speaks by default, so nothing is converted twice on the way out.
                output_format=ELEVENLABS_WIRE_FORMAT,
            )
        case SpeechProviderName.GPT_LIVE:
            endpoint, model = settings.require_speech_model()
            return GptLiveSpeechProvider(
                wrap(gpt_live_opener(endpoint, api_key=api_key)),
                metrics,
                model=model,
                languages=settings.speech_languages,
                input_formats=_SPEECH_INPUT_FORMATS,
                # A phone call's own format, so its audio is converted neither in nor out.
                output_format=TELEPHONY_NARROWBAND,
            )
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            assert_never(unknown)


# How a user is found by the number they signed in with, for a transport that needs to.
type FindUser = Callable[[PhoneNumber], Awaitable[UserId | None]]


@dataclass(frozen=True, slots=True)
class CallTransportBinding:
    """One line's transport, its routes, how to release it, and whose calls are whose."""

    transport: CallTransport
    router: APIRouter
    close: Callable[[], Awaitable[None]]
    ownership: Callable[[FindUser], CallOwnership]


def build_reported_calls() -> AndroidNativeCallTransport:
    """The application's one handset transport, which handsets' reports feed."""
    return AndroidNativeCallTransport()


def build_call_transports(
    settings: Settings,
    *,
    reported_calls: AndroidNativeCallTransport,
    observability: Observability,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[CallTransportBinding, ...]:
    """A binding for each configured line, possibly none; `http_transport` stands in for the API."""
    match settings.telephony_provider:
        case TelephonyProviderName.ANDROID_NATIVE:
            # Handsets report over the application's own route, so this brings no routes.
            return (
                CallTransportBinding(
                    transport=reported_calls,
                    router=APIRouter(),
                    close=_nothing_to_close,
                    ownership=_reported_ownership,
                ),
            )
        case TelephonyProviderName.TWILIO | None:
            return tuple(
                _line_binding(
                    line,
                    unforwarded_line=settings.telephony_unforwarded_calls_owner,
                    observability=observability,
                    http_transport=http_transport,
                )
                for line in settings.require_telephony_lines()
            )
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            assert_never(unknown)


# Where a named line's callbacks are: `/lines/<name>/telephony/...`.
_LINES_PATH: Final = "/lines"


def _path_prefix(line: TelephonyLine) -> str:
    """What a line's callback paths begin with: nothing for the unnamed line, else its name."""
    return "" if line.name is None else f"{_LINES_PATH}/{line.name}"


def _line_binding(
    line: TelephonyLine,
    *,
    unforwarded_line: PhoneNumber | None,
    observability: Observability,
    http_transport: httpx.AsyncBaseTransport | None,
) -> CallTransportBinding:
    """A streaming line's transport, routes and ownership, by the provider it is an account with."""
    match line.provider:
        case LineProviderName.TWILIO:
            transport = TwilioCallTransport(
                config=TwilioConfig(
                    account_id=line.account_id,
                    app_id=line.app_id,
                    numbers=line.numbers,
                    path_prefix=_path_prefix(line),
                ),
                api=HttpTelephonyApi(
                    account_id=line.account_id,
                    auth_token=line.auth_token,
                    transport=http_transport,
                ),
                verifier=SignatureVerifier(
                    auth_token=line.auth_token, public_base_url=line.webhook_base_url
                ),
            )
            return CallTransportBinding(
                transport=transport,
                router=build_twilio_router(
                    transport, tracer=observability.tracer, metrics=observability.metrics
                ),
                close=transport.close,
                ownership=partial(
                    ForwardedCallOwnership, transport, unforwarded_line=unforwarded_line
                ),
            )
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            assert_never(unknown)


async def _nothing_to_close() -> None:
    return None


def _reported_ownership(_find_user: FindUser) -> CallOwnership:
    # A handset's report names its account already; nobody needs finding by number.
    return ReportedCallOwnership()


def build_call_orchestrator(
    settings: Settings,
    *,
    container: Container,
    session_factory: async_sessionmaker[AsyncSession],
    telephony: Sequence[CallTransportBinding],
    dispatcher: EscalationDispatcher,
    observability: Observability,
    assistant: AssistantServices | None = None,
    summariser: CallSummariser | None = None,
) -> CallOrchestrator:
    """The orchestrator for these lines; `assistant` and `summariser` override what is built."""
    clock = container.clock
    cipher = _sealing(container, "carry calls: every call is recorded, sealed")

    @asynccontextmanager
    async def stores() -> AsyncIterator[CallStores]:
        async with unit_of_work(session_factory) as session:
            yield CallStores(
                users=SqlUserRepository(session, clock),
                preferences=SqlPreferencesRepository(session, clock),
                calls=SqlCallRepository(session, cipher, clock),
                transcripts=SqlTranscriptRepository(session, cipher),
                summaries=SqlSummaryRepository(session, cipher, clock),
                timeline=SqlCallTimelineRepository(session),
            )

    async def find_user(number: PhoneNumber) -> UserId | None:
        async with unit_of_work(session_factory) as session:
            user = await SqlUserRepository(session, clock).find_by_number(number)
        return None if user is None else user.id

    takes_calls = any(
        binding.transport.capabilities.supports_agent_conversation
        and binding.transport.capabilities.can_answer_under_program_control
        for binding in telephony
    )
    if assistant is None and takes_calls:
        assistant = AssistantServices(
            speech=build_speech_provider(settings, metrics=observability.metrics),
            voices=container.voices,
            judging=lambda actions: build_call_judging(settings, actions=actions),
        )
    # One set of bounds, shared by the summariser and teardown.
    bounds = call_bounds(settings)
    if summariser is None and settings.llm_configured:
        summariser = build_call_summariser(
            settings, timeout=bounds.summary, metrics=observability.metrics
        )
    return CallOrchestrator(
        lines=[CallLine(binding.transport, binding.ownership(find_user)) for binding in telephony],
        stores=stores,
        dispatcher=dispatcher,
        clock=clock,
        metrics=observability.metrics,
        tracer=observability.tracer,
        circuits=observability.circuits,
        assistant=assistant,
        summariser=summariser,
        bounds=bounds,
    )


def call_bounds(settings: Settings) -> Bounds:
    """How long anything on a call may take, with how long a call may last as configured."""
    return Bounds(duration=timedelta(seconds=settings.call_max_duration_seconds))


def build_call_judging(settings: Settings, *, actions: CallActions) -> CallJudging:
    """The agent that judges calls, on the configured model, acting through `actions`."""
    endpoint = settings.require_llm()
    return call_judging_on(
        openai_compatible_model(endpoint),
        actions=actions,
        timeout=timedelta(seconds=endpoint.timeout_seconds),
    )


def call_judging_on(model: Model, *, actions: CallActions, timeout: timedelta) -> CallJudging:
    """The agent on `model`, with its tools, conclusion and one escalation service for the call."""
    escalation = EscalationService(actions)
    return CallJudging(
        agent=StrandsCallAgent(
            model,
            tools=tools_for_judgements(actions),
            conclusion=JudgementConclusion(actions, escalation),
            timeout=timeout,
        ),
        forget=escalation.forget,
    )


def build_call_summariser(
    settings: Settings, *, timeout: timedelta, metrics: MetricsRecorder
) -> CallSummariser:
    """What summarises an ended call, on the agent's model, within teardown's `timeout`."""
    return call_summariser_on(
        openai_compatible_model(settings.require_llm()), timeout=timeout, metrics=metrics
    )


def call_summariser_on(
    model: Model, *, timeout: timedelta, metrics: MetricsRecorder
) -> CallSummariser:
    """The summariser on `model`."""
    return ModelCallSummariser(StrandsSummaryDrafter(model), timeout=timeout, metrics=metrics)


def _unwrapped(opener: ConnectionOpener) -> ConnectionOpener:
    return opener


def _build_otp_provider(settings: Settings) -> OTPProvider:
    """The sign-in code provider: the default, and each calling code's own, each built once."""
    routes = settings.otp_provider_by_calling_code
    built = {
        name: _otp_provider_named(name, settings)
        for name in {settings.otp_provider, *(name for _, name in routes)}
    }
    default = built[settings.otp_provider]
    if not routes:
        return default
    return OTPProviderByCallingCode(
        default=default, by_calling_code={code: built[name] for code, name in routes}
    )


def _otp_provider_named(name: OTPProviderName, settings: Settings) -> OTPProvider:
    """The provider called `name`, built from its own settings."""
    match name:
        case OTPProviderName.MOCK:
            return MockOTPProvider(is_production=settings.is_production)
        case OTPProviderName.TWILIO_SMS:
            account = settings.require_sms_account()
            return SmsOTPProvider(
                account_id=account.account_id,
                auth_token=account.auth_token,
                sender=account.sender,
            )
        case OTPProviderName.TWILIO_VERIFY:
            verify = settings.require_verify_account()
            return VerifyOTPProvider(
                account_id=verify.account_id,
                auth_token=verify.auth_token,
                service_id=verify.service_id,
            )
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            # Makes the type checker reject a provider that has no case here.
            assert_never(unknown)
