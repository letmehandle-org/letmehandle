"""What the composition root tells a speech service, before any audio flows."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from letmehandle.bootstrap import build_speech_provider
from letmehandle.config.settings import ConfigurationError, SpeechProviderName
from letmehandle.domain.models.audio import SPEECH_WIDEBAND, TELEPHONY_NARROWBAND
from tests.support.config import EXAMPLE_DEFAULT_VOICE, make_settings
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )


class Listening:
    """A connection that keeps what it is sent and says nothing back until closed."""

    def __init__(self) -> None:
        self.sent: list[Mapping[str, Any]] = []
        self._closed = asyncio.Event()

    async def send(self, event: Mapping[str, Any]) -> None:
        self.sent.append(event)

    async def receive(self) -> Mapping[str, Any] | None:
        await self._closed.wait()
        return None

    async def close(self) -> None:
        self._closed.set()


async def configuration_sent(transcription_model: str | None) -> Mapping[str, Any]:
    connection = Listening()

    def instead_of_the_network(_opener: ConnectionOpener) -> ConnectionOpener:
        async def open_listening() -> EventConnection:
            return connection

        return open_listening

    settings = make_settings(
        speech_endpoint_url="wss://speech.example.com/v1/realtime",
        speech_model="a-model",
        speech_transcription_model=transcription_model,
    )
    assert settings.speech_provider is SpeechProviderName.REALTIME
    provider = build_speech_provider(
        settings, metrics=RecordingMetrics(), wrap_connection=instead_of_the_network
    )
    async with await provider.connect(
        system_context="You answer calls.",
        voice_id=EXAMPLE_DEFAULT_VOICE,
        greeting="Hello.",
        locale="en",
        input_format=SPEECH_WIDEBAND,
    ):
        pass
    [configuration] = [event for event in connection.sent if event["type"] == "session.update"]
    return configuration


async def test_a_configured_transcription_model_is_asked_for() -> None:
    # The caller's words are transcribed as well as the assistant's.
    configuration = await configuration_sent("a-transcription-model")
    transcription = configuration["session"]["audio"]["input"]["transcription"]
    assert transcription["model"] == "a-transcription-model"


async def test_without_one_no_transcription_is_requested() -> None:
    configuration = await configuration_sent(None)
    assert "transcription" not in configuration["session"]["audio"]["input"]


def test_the_speech_service_is_offered_the_languages_the_settings_list() -> None:
    settings = make_settings(
        speech_provider=SpeechProviderName.ELEVENLABS,
        speech_endpoint_url="wss://speech.example.com/v1/convai/conversation",
        speech_agent_id="an-agent",
        speech_languages=("en", "hi"),
    )
    provider = build_speech_provider(settings, metrics=RecordingMetrics())
    assert provider.capabilities.languages == ("en", "hi")
    assert provider.capabilities.speaks("hi-IN")


class Starting(Listening):
    """A connection that starts a session when asked to, and finalises one when asked to."""

    def __init__(self) -> None:
        super().__init__()
        self._answers: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue()

    async def send(self, event: Mapping[str, Any]) -> None:
        await super().send(event)
        if event["type"] == "session.start":
            self._answers.put_nowait({"type": "session.started"})
        elif event["type"] == "session.close":
            self._answers.put_nowait({"type": "session.closed", "reason": "close_requested"})
            self._answers.put_nowait(None)

    async def receive(self) -> Mapping[str, Any] | None:
        return await self._answers.get()


async def test_gpt_live_is_chosen_by_configuration_alone() -> None:
    connection = Starting()

    def instead_of_the_network(_opener: ConnectionOpener) -> ConnectionOpener:
        async def open_starting() -> EventConnection:
            return connection

        return open_starting

    settings = make_settings(
        speech_provider=SpeechProviderName.GPT_LIVE,
        speech_endpoint_url="wss://speech.example.com/v1/live/sessions",
        speech_model="a-live-model",
        speech_languages=("en", "hi"),
    )
    provider = build_speech_provider(
        settings, metrics=RecordingMetrics(), wrap_connection=instead_of_the_network
    )
    assert provider.name == "gpt_live"
    assert provider.capabilities.speaks("hi-IN")
    async with await provider.connect(
        system_context="You answer calls.",
        voice_id=EXAMPLE_DEFAULT_VOICE,
        greeting="Hello.",
        locale="en",
        input_format=TELEPHONY_NARROWBAND,
    ):
        pass
    start = connection.sent[0]["session"]
    assert start["model"] == "a-live-model"
    assert start["audio"]["format"] == {"type": "audio/pcmu", "rate": 8000}
    assert connection.sent[-1] == {"type": "session.close"}


def test_gpt_live_without_a_model_names_what_is_missing() -> None:
    settings = make_settings(
        speech_provider=SpeechProviderName.GPT_LIVE,
        speech_endpoint_url="wss://speech.example.com/v1/live/sessions",
    )
    with pytest.raises(ConfigurationError, match="SPEECH_MODEL"):
        build_speech_provider(settings, metrics=RecordingMetrics())
