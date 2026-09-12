"""The composition root.

The one place that decides which implementation each port gets. Nothing above this learns which
it was given, which is what makes a provider replaceable by editing one file.

It is also the only module permitted to name a provider. A test asserts that no module under
`domain/` does, and the reason this file is exempt is that choosing is precisely its job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Final, assert_never

from letmehandle.adapters.agent.strands.agent import StrandsCallAgent
from letmehandle.adapters.agent.strands.model import openai_compatible_model
from letmehandle.adapters.clock import SystemClock, UUIDGenerator
from letmehandle.adapters.otp.mock import MockOTPProvider
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
from letmehandle.adapters.speech.realtime.protocol import WIRE_FORMAT as REALTIME_WIRE_FORMAT
from letmehandle.adapters.speech.realtime.provider import RealtimeSpeechProvider
from letmehandle.adapters.speech.realtime.websocket import websocket_opener as realtime_opener
from letmehandle.adapters.voice.builtin import BuiltInVoiceProvider
from letmehandle.application.agent.conclusion import JudgementConclusion
from letmehandle.application.agent.escalation import EscalationService
from letmehandle.application.agent.tools.registry import tools_for_judgements
from letmehandle.config.settings import OTPProviderName, Settings, SpeechProviderName
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND

if TYPE_CHECKING:
    from collections.abc import Callable

    from strands.models.model import Model

    from letmehandle.adapters.speech.websocket.connection import ConnectionOpener
    from letmehandle.application.agent.ports import CallActions, CallAgent
    from letmehandle.domain.ports.clock import Clock, IdGenerator
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.otp import OTPProvider
    from letmehandle.domain.ports.rate_limit import RateLimiter
    from letmehandle.domain.ports.security import (
        SecretGenerator,
        SecretHasher,
        TokenSigner,
        TranscriptCipher,
    )
    from letmehandle.domain.ports.speech import SpeechProvider
    from letmehandle.domain.ports.voice import VoiceProvider


@dataclass(frozen=True, slots=True)
class Container:
    """Everything chosen at startup, held for the life of the process.

    Two hashers, deliberately. A one-time code is salted and slow, because it is checked against
    one row and a cheap hash is one worth attacking offline. A refresh token is hashed with a
    keyed, deterministic hash, because it has to be *found* — and a salted hash would turn that
    lookup into a scan of every row in the table.
    """

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
    # None when no transcript keys are configured. Call history cannot be read without them, and
    # its routes say so; everything else, which never opens a sealed record, runs regardless.
    transcript_cipher: TranscriptCipher | None


def build_container(settings: Settings, *, voices: VoiceProvider) -> Container:
    """Choose the implementations for this configuration.

    The voice provider is passed in rather than chosen here because it is needed earlier
    than the rest: which routes the application has depends on what it can do, and routing
    is settled before anything starts. Handing the same instance on is what stops a second
    one being built that could answer differently.
    """
    clock = SystemClock()
    signing_key = settings.require_signing_key()

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
        refresh_token_lifetime=timedelta(seconds=settings.auth_refresh_token_ttl_seconds),
        transcript_cipher=(
            None
            if settings.transcript_encryption_keys is None
            else AesGcmTranscriptCipher(settings.require_transcript_keys())
        ),
    )


def build_voice_provider(settings: Settings) -> VoiceProvider:
    """Which voices this deployment offers: the catalogue its speech service speaks.

    Read from configuration rather than written anywhere in code, because the service decides
    which voices exist and a list of its own here would offer voices it cannot speak.
    """
    voices, default_voice = settings.require_voice_catalogue()
    return BuiltInVoiceProvider(voices, default_voice_id=default_voice)


# English only in the first release (D-017). A setting arrives with the second language, not
# before it.
_SPEECH_LANGUAGES: Final = ("en",)

# What the speech adapters can convert from: a microphone's wideband audio, and a phone line's.
_SPEECH_INPUT_FORMATS: Final = (SPEECH_WIDEBAND, TELEPHONY_NARROWBAND)


def build_speech_provider(
    settings: Settings,
    *,
    metrics: MetricsRecorder,
    wrap_connection: Callable[[ConnectionOpener], ConnectionOpener] | None = None,
) -> SpeechProvider:
    """The speech service this deployment talks to, by the protocol it speaks.

    `wrap_connection` lets a caller stand between the session and the network — the harness uses
    it to drop a connection on command and watch the session recover — without that caller
    constructing the adapter itself.

    A match with an exhaustiveness check, for the reason `_build_otp_provider` gives: a protocol
    added to the settings without an adapter chosen here fails to type-check.
    """
    wrap = wrap_connection or _unwrapped
    key = settings.speech_api_key
    api_key = None if key is None else key.get_secret_value()
    match settings.speech_provider:
        case SpeechProviderName.REALTIME:
            endpoint, model = settings.require_speech_service()
            return RealtimeSpeechProvider(
                wrap(realtime_opener(endpoint, model=model, api_key=api_key)),
                metrics,
                languages=_SPEECH_LANGUAGES,
                input_formats=_SPEECH_INPUT_FORMATS,
                # The protocol's own wire format, so that nothing is converted twice on its way
                # out. A sink converts to what it plays.
                output_format=REALTIME_WIRE_FORMAT,
                transcription_model=settings.speech_transcription_model,
            )
        case SpeechProviderName.ELEVENLABS:
            endpoint, agent_id = settings.require_speech_agent()
            return ElevenLabsSpeechProvider(
                wrap(elevenlabs_opener(endpoint, agent_id=agent_id, api_key=api_key)),
                metrics,
                languages=_SPEECH_LANGUAGES,
                input_formats=_SPEECH_INPUT_FORMATS,
                # What an agent speaks unless configured otherwise, so that an agent left at its
                # default is not converted twice on the way out either.
                output_format=ELEVENLABS_WIRE_FORMAT,
            )
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            assert_never(unknown)


def build_call_agent(settings: Settings, *, actions: CallActions) -> CallAgent:
    """The agent that judges calls, on the model this deployment is configured with.

    The call's actions are handed in rather than built here, because only orchestration holds a
    call. What is chosen here is the framework and the model.
    """
    endpoint = settings.require_llm()
    return call_agent_on(
        openai_compatible_model(endpoint),
        actions=actions,
        timeout=timedelta(seconds=endpoint.timeout_seconds),
    )


def call_agent_on(model: Model, *, actions: CallActions, timeout: timedelta) -> CallAgent:
    """The agent on `model`, with its tools and the conclusion that acts on what they asked for.

    Built once, here, so every judgement on a call goes through one escalation service and one
    memory of whether the user was reached. Tests reach the same wiring with a scripted model.
    """
    return StrandsCallAgent(
        model,
        tools=tools_for_judgements(actions),
        conclusion=JudgementConclusion(actions, EscalationService(actions)),
        timeout=timeout,
    )


def _unwrapped(opener: ConnectionOpener) -> ConnectionOpener:
    return opener


def _build_otp_provider(settings: Settings) -> OTPProvider:
    """Which provider delivers sign-in codes.

    A match with an exhaustiveness check rather than a dictionary with a default: adding a
    provider without deciding what it is called here fails to type-check, instead of quietly
    falling through to the mock — which is the one failure that must never happen silently.
    """
    match settings.otp_provider:
        case OTPProviderName.MOCK:
            return MockOTPProvider(is_production=settings.is_production)
        case unknown:  # pragma: no cover - unreachable while every member has a case above
            # Not dead code: it is what makes the type checker reject a new provider that has
            # not been wired in here. Unreachable at run time is exactly the point.
            assert_never(unknown)
