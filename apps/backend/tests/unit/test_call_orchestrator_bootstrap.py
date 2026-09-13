"""One orchestrator, for the configured transport, started and stopped with the application."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from letmehandle.application.calls.summariser import ModelCallSummariser
from letmehandle.application.orchestration.ports import AssistantServices
from letmehandle.bootstrap import call_bounds
from letmehandle.config.settings import (
    ConfigurationError,
    Settings,
    TelephonyProviderName,
    get_settings,
)
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind
from letmehandle.main import create_app
from tests.contracts.fakes import StaticVoiceProvider
from tests.support.config import (
    REQUIRED_ENVIRONMENT,
    TEST_TRANSCRIPT_KEYS,
    UNREACHABLE_DATABASE,
    make_settings,
)
from tests.support.orchestration import Agent, ControlledSpeech, eventually, running_tasks
from tests.unit.test_call_transport_bootstrap import telephony_settings


def with_storage(settings: Settings) -> Settings:
    """The same deployment, with a database to record calls in and the keys to seal them."""
    stored = make_settings(
        database_url=UNREACHABLE_DATABASE, transcript_encryption_keys=TEST_TRANSCRIPT_KEYS
    )
    return settings.model_copy(
        update={
            "database_url": stored.database_url,
            "transcript_encryption_keys": stored.transcript_encryption_keys,
        }
    )


async def test_a_deployment_with_calls_and_storage_orchestrates_them_until_it_stops() -> None:
    before = running_tasks()
    app = create_app(
        with_storage(make_settings(telephony_provider=TelephonyProviderName.ANDROID_NATIVE))
    )
    async with app.router.lifespan_context(app):
        orchestrator = app.state.orchestrator
        assert orchestrator is not None
        # A call arrives while storage is unreachable: nobody can be found to own it, so it is
        # let go rather than held, and the run is gone.
        await app.state.reported_calls.publish(
            UserId("user"),
            CallEvent(CallEventKind.INCOMING, CallId("user:call"), EventId("user:event")),
        )
        await eventually(lambda: orchestrator.live_calls == 0)
    assert app.state.orchestrator is None
    await eventually(lambda: not running_tasks() - before)


async def test_a_deployment_that_cannot_seal_calls_refuses_to_start() -> None:
    app = create_app(
        make_settings(
            telephony_provider=TelephonyProviderName.ANDROID_NATIVE,
            database_url=UNREACHABLE_DATABASE,
        )
    )
    with pytest.raises(ConfigurationError, match="TRANSCRIPT_ENCRYPTION_KEYS"):
        async with app.router.lifespan_context(app):
            pass
    assert app.state.engine is None


async def test_a_streaming_deployment_without_a_speech_service_refuses_to_start() -> None:
    settings = with_storage(telephony_settings())
    app = create_app(settings)
    with pytest.raises(ConfigurationError, match="SPEECH_ENDPOINT_URL"):
        async with app.router.lifespan_context(app):
            pass


async def test_a_streaming_deployment_takes_calls_with_the_assistant_it_is_given() -> None:
    settings = with_storage(telephony_settings())
    speech = ControlledSpeech()
    app = create_app(
        settings,
        assistant=AssistantServices(
            speech=speech, voices=StaticVoiceProvider(), judging=Agent().judging
        ),
    )
    async with app.router.lifespan_context(app):
        assert app.state.orchestrator is not None
        # No model is configured, so every call is summarised from its facts.
        assert app.state.orchestrator._context.summariser is None
        await asyncio.sleep(0)
    assert speech.sessions == []


async def test_a_streaming_deployment_builds_its_speech_service_and_agent_from_settings() -> None:
    settings = with_storage(telephony_settings()).model_copy(
        update={
            field: getattr(
                make_settings(
                    speech_endpoint_url="wss://speech.example.com/v1/realtime",
                    speech_model="example-speech-model",
                    llm_base_url="https://model.example.com/v1",
                    llm_api_key="an-example-key",
                    llm_model="an-example-model",
                ),
                field,
            )
            for field in (
                "speech_endpoint_url",
                "speech_model",
                "llm_base_url",
                "llm_api_key",
                "llm_model",
            )
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert app.state.orchestrator is not None
        assert isinstance(app.state.orchestrator._context.summariser, ModelCallSummariser)


def test_how_long_a_call_may_last_is_the_configured_bound() -> None:
    assert call_bounds(make_settings()).duration == timedelta(hours=4)
    assert call_bounds(make_settings(call_max_duration_seconds=600)).duration == timedelta(
        minutes=10
    )


@pytest.mark.parametrize("seconds", ["30", "86401", "forever"])
def test_a_call_duration_outside_a_minute_to_a_day_stops_startup(
    monkeypatch: pytest.MonkeyPatch, seconds: str
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CALL_MAX_DURATION_SECONDS", seconds)
    with pytest.raises(ConfigurationError, match="CALL_MAX_DURATION_SECONDS"):
        get_settings()
