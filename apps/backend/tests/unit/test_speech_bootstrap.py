"""What the composition root tells a speech service, before any audio flows."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from letmehandle.bootstrap import build_speech_provider
from letmehandle.config.settings import SpeechProviderName
from letmehandle.domain.models.audio import SPEECH_WIDEBAND
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
        locale="en",
        input_format=SPEECH_WIDEBAND,
    ):
        pass
    [configuration] = [event for event in connection.sent if event["type"] == "session.update"]
    return configuration


async def test_a_configured_transcription_model_is_asked_for() -> None:
    # Without it the caller is answered but never written down, and the record of the call holds
    # only the assistant's side.
    configuration = await configuration_sent("a-transcription-model")
    transcription = configuration["session"]["audio"]["input"]["transcription"]
    assert transcription["model"] == "a-transcription-model"


async def test_without_one_no_transcription_is_requested() -> None:
    configuration = await configuration_sent(None)
    assert "transcription" not in configuration["session"]["audio"]["input"]
